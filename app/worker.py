"""
Celery worker — all scraping, normalization and persistence tasks.

Task pipeline per source:
  orchestrate_scrape_task
       │
       ├── dump_to_s3_task  (fire-and-forget raw backup)
       │
       └── process_listings_batch_task (batches of 25)
                │
                └── normalize_and_persist_task (per new/changed listing)
"""
import asyncio
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import boto3
from celery import Celery
from dotenv import load_dotenv

from .database import SessionLocal
from .models import ScrapeLog, ProductListing, Product, PriceHistory, Source
from scrapers.factory import ScraperFactory
from .ai_pipeline import normalize_via_bedrock
from sqlalchemy.exc import IntegrityError

load_dotenv()

log = logging.getLogger(__name__)

celery_app = Celery(
    "perfume_worker",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
)
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.timezone = "America/Santiago"
celery_app.conf.task_acks_late = True          # Requeue on worker crash

# ── S3 Client ────────────────────────────────────────────────────────────────

s3_client = boto3.client("s3", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
BUCKET_NAME = os.getenv("S3_RAW_BUCKET", "perfume-intelligence-platform")


# ── Task 1: Orchestrator ─────────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=2, default_retry_delay=60)
def orchestrate_scrape_task(self, source_id: str, limit: int = None):
    """
    Entry point for a full source scrape.
    1. Instantiates the correct scraper strategy via factory
    2. Runs the async catalog extraction
    3. Backs up raw data to S3
    4. Dispatches processing batches to the queue
    """
    db = SessionLocal()
    scrape_log = None
    try:
        source = db.query(Source).filter(Source.id == source_id).first()
        if not source:
            return {"error": f"Source {source_id} not found"}

        if not source.is_active:
            return {"skipped": f"Source {source.name} is inactive"}

        scrape_log = ScrapeLog(source_id=source.id, status="STARTED")
        db.add(scrape_log)
        db.commit()
        db.refresh(scrape_log)

        scraper = ScraperFactory.get_scraper(
            source.engine_type, source.base_url, str(source.id), source.config
        )
        raw_catalog = asyncio.run(scraper.extract_catalog())

        if limit:
            raw_catalog = raw_catalog[:limit]

        total = len(raw_catalog)
        log.info(f"[{source.name}] Extracted {total} raw listings")

        # S3 backup (non-blocking — if fails, pipeline continues)
        s3_uri = _dump_to_s3(raw_catalog, str(source.id))

        scrape_log.s3_raw_uri = s3_uri
        scrape_log.records_extracted = total
        scrape_log.status = "SUCCESS"
        scrape_log.ended_at = datetime.now(timezone.utc)
        db.commit()

        # Dispatch in batches of 25 to keep each task fast
        batch_size = 25
        batches = [raw_catalog[i : i + batch_size] for i in range(0, total, batch_size)]
        for batch in batches:
            process_listings_batch_task.delay(batch, str(source.id))

        return {
            "source": source.name,
            "extracted": total,
            "batches_dispatched": len(batches),
            "s3_uri": s3_uri,
        }

    except Exception as exc:
        db.rollback()
        if scrape_log:
            scrape_log.status = "FAIL"
            scrape_log.error_message = str(exc)[:1000]
            scrape_log.ended_at = datetime.now(timezone.utc)
            db.commit()
        log.error(f"orchestrate_scrape_task failed for {source_id}: {exc}", exc_info=True)
        raise self.retry(exc=exc)
    finally:
        db.close()


# ── S3 Backup (non-Celery helper) ────────────────────────────────────────────

def _dump_to_s3(raw_data: list, source_id: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M-%S")
    key = f"raw/{source_id}/{timestamp}.json"
    try:
        with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as f:
            json.dump(raw_data, f, ensure_ascii=False)
            tmp_name = f.name
            
        with open(tmp_name, 'rb') as f:
            s3_client.put_object(
                Bucket=BUCKET_NAME,
                Key=key,
                Body=f,
                ContentType="application/json",
            )
        os.unlink(tmp_name)
        return f"s3://{BUCKET_NAME}/{key}"
    except Exception as e:
        log.warning(f"S3 backup failed (non-fatal): {e}")
        return ""


# ── Task 2: Batch Hash Check ─────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def process_listings_batch_task(self, batch: list, source_id: str):
    """
    For each raw listing in a batch:
    - Compute SHA-256 state hash of (price, title, barcode)
    - Skip if hash matches stored hash (data unchanged)
    - Dispatch normalize_and_persist_task for new/changed listings
    """
    db = SessionLocal()
    try:
        for item in batch:
            raw_title = item.get("raw_title", "") or ""
            price = item.get("price", 0.0) or 0.0
            barcode = item.get("barcode", "") or ""
            sku = item.get("sku", "") or ""
            listing_url = item.get("url", "") or ""

            # Prefer SKU as external ID; fall back to title + url hash
            source_external_id = sku if sku else _title_id(raw_title, listing_url)

            state_string = f"{raw_title}-{price}-{barcode}-{stock}".encode("utf-8")
            current_hash = hashlib.sha256(state_string).hexdigest()

            # O(1) lookup: does this listing exist and is it unchanged?
            existing = db.query(ProductListing).filter(
                ProductListing.source_id == source_id,
                ProductListing.source_external_id == source_external_id,
            ).first()

            if existing and existing.current_hash == current_hash:
                continue  # No change — skip expensive AI + DB write

            # New or changed → normalize and persist
            item["_source_external_id"] = source_external_id
            item["_current_hash"] = current_hash
            normalize_and_persist_task.delay(item, source_id)

    except Exception as exc:
        db.rollback()
        log.error(f"process_listings_batch_task failed: {exc}", exc_info=True)
        raise self.retry(exc=exc)
    finally:
        db.close()


def _title_id(title: str, url: str = "") -> str:
    """Generate a stable short ID from title and url for use as source_external_id."""
    return hashlib.sha256(f"{title}-{url}".encode("utf-8")).hexdigest()[:32]


# ── Task 3: Normalize + Persist ───────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def normalize_and_persist_task(self, raw_listing: dict, source_id: str):
    """
    1. Run AI normalization on the raw listing
    2. Deduplicate against canonical Products table
    3. Upsert ProductListing with all available fields
    4. Append PriceHistory record
    """
    db = SessionLocal()
    try:
        raw_title = raw_listing.get("raw_title", "") or ""
        listing_url = raw_listing.get("url", "") or ""
        source_external_id = raw_listing.get("_source_external_id") or _title_id(raw_title, listing_url)
        current_hash = raw_listing.get("_current_hash", "")

        # ── Step 1: AI Normalization ─────────────────────────────────────
        clean_data = normalize_via_bedrock(raw_listing)

        brand = (clean_data.get("brand") or "").strip() or "Unknown"
        product_name = (clean_data.get("product_name") or "").strip() or raw_title[:100]
        variant = clean_data.get("variant")
        fragrance_type = clean_data.get("fragrance_type")
        ml = clean_data.get("ml")
        gender = clean_data.get("gender")
        ean_13 = clean_data.get("ean_13")

        # ── STRICT PERFUME VALIDATION ────────────────────────────────────
        # The database is exclusively for perfumes. Discard cosmetics, hair
        # care, and other non-fragrance items lacking core attributes.
        if not ml or not fragrance_type:
            log.info(f"Skipped non-perfume item (missing ml or fragrance_type): {raw_title}")
            return
            
        # ── Step 2: Canonical Product Deduplication ───────────────────────
        # Primary: exact EAN-13 match
        canonical_product = None
        if ean_13:
            canonical_product = db.query(Product).filter(
                Product.ean_13 == ean_13
            ).first()

        # Secondary: case-insensitive brand + name + ml match
        if not canonical_product:
            q = db.query(Product).filter(
                Product.brand.ilike(brand),
                Product.product_name.ilike(product_name),
            )
            if ml:
                q = q.filter(Product.ml == ml)
            else:
                q = q.filter(Product.ml.is_(None))
                
            if variant:
                q = q.filter(Product.variant == variant)
            else:
                q = q.filter(Product.variant.is_(None))
                
            canonical_product = q.first()

        if not canonical_product:
            canonical_product = Product(
                brand=brand,
                product_name=product_name,
                variant=variant,
                fragrance_type=fragrance_type,
                ml=ml,
                gender=gender,
                ean_13=ean_13 if ean_13 else None,
            )
            db.add(canonical_product)
            db.flush()  # Get ID without full commit
        else:
            # Enrich existing product with newly discovered fields
            if not canonical_product.fragrance_type and fragrance_type:
                canonical_product.fragrance_type = fragrance_type
            if not canonical_product.ml and ml:
                canonical_product.ml = ml
            if not canonical_product.gender and gender:
                canonical_product.gender = gender
            if not canonical_product.ean_13 and ean_13:
                canonical_product.ean_13 = ean_13

        # ── Step 3: Upsert ProductListing ────────────────────────────────
        price_raw = raw_listing.get("price", 0.0)
        try:
            current_price = Decimal(str(price_raw))
        except InvalidOperation:
            current_price = None

        listing = db.query(ProductListing).filter(
            ProductListing.source_id == source_id,
            ProductListing.source_external_id == source_external_id,
        ).first()

        listing_url = raw_listing.get("url", "") or ""
        image_url = raw_listing.get("image_url", "") or None
        stock = raw_listing.get("stock")
        is_available = raw_listing.get("available", True) if current_price is not None else False

        if not listing:
            listing = ProductListing(
                product_id=canonical_product.id,
                source_id=source_id,
                source_external_id=source_external_id,
                title=raw_title,
                url=listing_url,
                image_url=image_url,
                current_hash=current_hash,
                current_price=current_price,
                current_stock=stock,
                is_available=is_available,
            )
            db.add(listing)
        else:
            listing.title = raw_title
            listing.current_hash = current_hash
            listing.current_price = current_price
            listing.current_stock = stock
            listing.is_available = is_available
            listing.last_seen_at = datetime.now(timezone.utc)
            if listing_url:
                listing.url = listing_url
            if image_url:
                listing.image_url = image_url

        db.commit()
        db.refresh(listing)

        # ── Step 4: Append Price History ─────────────────────────────────
        if current_price is not None:
            history = PriceHistory(
                listing_id=listing.id,
                price=current_price,
                stock=stock,
            )
            db.add(history)
            db.commit()

        log.info(f"Persisted: [{brand}] {product_name} {ml}ml ({fragrance_type}) @ {current_price}")

    except IntegrityError as e:
        db.rollback()
        log.warning(f"Integrity error (likely duplicate) for '{raw_title}', retrying: {e}")
        raise self.retry(exc=e)
    except Exception as exc:
        db.rollback()
        log.error(f"normalize_and_persist_task failed for '{raw_title}': {exc}", exc_info=True)
        raise self.retry(exc=exc)
    finally:
        db.close()