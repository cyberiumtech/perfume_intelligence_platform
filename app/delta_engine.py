"""
Delta Engine for the Perfume Intelligence Platform.

Core update logic that replaces the buggy worker.py.

Architecture: One Product, Many Listings
- products table: canonical identity ONLY
- product_listings table: one row PER source PER product variant
- Comparison happens across listings, not by collapsing them

Hash definition: SHA256(str(price) + "|" + str(stock) + "|" + availability)
  - Title EXCLUDED from hash (cosmetic changes don't trigger re-processing)
  - Fix: stock is now properly read before hash computation

Pipeline per listing:
  1. Extract stock with per-source-type strategy
  2. Determine stock confidence
  3. Compute state hash
  4. If hash matches existing listing → skip (records_skipped++)
  5. If hash differs → update listing + insert price_history (records_updated++)
  6. If no listing exists → normalize, deduplicate, create product, create listing

Availability logic (B2B-first):
  - stock > 0 → AVAILABLE_IN_STOCK
  - stock == 0 → OUT_OF_STOCK
  - stock is None → UNKNOWN (do NOT default to available)

All operations within a single transaction — no double-commit bug.
"""
import hashlib
import logging
import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Optional, List

from sqlalchemy.orm import Session
from sqlalchemy import func

from .models import (
    Product, ProductListing, PriceHistory, Source, ScrapeLog,
    AvailabilityState, NormalizationMethod, FragranceType, GenderType,
    StockConfidence, BusinessType,
)
from .normalization import normalize_product, NormalizedProduct
from .exceptions import (
    PerfumePlatformError, DatabaseError, IntegrityError as AppIntegrityError,
)

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# HASH COMPUTATION
# ══════════════════════════════════════════════════════════════════════════════

def compute_state_hash(price: Any, stock: Any, availability: str) -> str:
    """
    Compute SHA-256 hash of the listing state.

    Hash = SHA256(price | stock | availability)
    Title is EXCLUDED — cosmetic changes should NOT trigger re-processing.

    Args:
        price: Current price (any type, will be stringified)
        stock: Current stock count (any type, will be stringified)
        availability: Availability state string

    Returns:
        64-char hex digest
    """
    state_string = f"{price}|{stock}|{availability}"
    return hashlib.sha256(state_string.encode("utf-8")).hexdigest()


def _title_id(title: str, url: str = "") -> str:
    """Generate a stable short ID from title+url for source_external_id fallback."""
    return hashlib.sha256(f"{title}-{url}".encode("utf-8")).hexdigest()[:32]


# ══════════════════════════════════════════════════════════════════════════════
# STOCK EXTRACTION (Per-Source-Type)
# ══════════════════════════════════════════════════════════════════════════════

def extract_stock(raw_item: Dict[str, Any], source_type: str = None) -> Optional[int]:
    """
    Extract stock count from raw listing data using per-source-type strategy.

    Returns int stock count or None.

    Args:
        raw_item: Raw listing dict from scraper
        source_type: Engine type string (shopify, bs4_woocommerce, etc.)
    """
    # Shopify B2B / Wholesale — has inventory_quantity
    if source_type == "shopify":
        qty = raw_item.get("inventory_quantity")
        if qty is not None:
            try:
                return int(qty)
            except (ValueError, TypeError):
                pass
        # Fall through to generic extraction

    # WooCommerce B2B — has stock_text
    if source_type in ("bs4_woocommerce", "woocommerce"):
        stock_text = raw_item.get("stock_text", "")
        if stock_text and isinstance(stock_text, str):
            lower_text = stock_text.lower()
            if "out of stock" in lower_text:
                return 0
            match = re.search(r'(\d+)\s+in stock', lower_text)
            if match:
                return int(match.group(1))
            if "in stock" in lower_text:
                return 1  # At least 1 if text says in stock
        # Fall through to generic extraction

    # Jumpseller
    if source_type == "bs4_jumpseller":
        stock_val = raw_item.get("stock")
        if isinstance(stock_val, int):
            return stock_val
        if isinstance(stock_val, str) and stock_val.isdigit():
            return int(stock_val)
        # Fall through

    # Generic / Unknown — try common field names
    stock_val = raw_item.get("stock")
    if isinstance(stock_val, int):
        return stock_val
    if isinstance(stock_val, str) and stock_val.isdigit():
        return int(stock_val)

    # Try inventory_quantity as fallback
    qty = raw_item.get("inventory_quantity")
    if isinstance(qty, int):
        return qty

    return None


def determine_stock_confidence(
    raw_item: Dict[str, Any],
    source: Source,
) -> StockConfidence:
    """
    Determine how much confidence we have in the stock data.

    HIGH: B2B wholesale source with real inventory_quantity
    MEDIUM: Stock text says "in stock" with number
    LOW: Only an "available" boolean flag
    UNKNOWN: No stock information at all
    """
    # Check for real inventory quantity from B2B source
    if (source.business_type == BusinessType.B2B_WHOLESALE or not source.business_type):
        qty = raw_item.get("inventory_quantity")
        if qty is not None:
            try:
                int(qty)
                return StockConfidence.HIGH
            except (ValueError, TypeError):
                pass

    # Check for stock text with number
    stock_text = raw_item.get("stock_text", "")
    if stock_text and isinstance(stock_text, str):
        lower_text = stock_text.lower()
        match = re.search(r'(\d+)\s+in stock', lower_text)
        if match:
            return StockConfidence.MEDIUM
        if "in stock" in lower_text:
            return StockConfidence.MEDIUM

    # Check for explicit stock count
    stock_val = raw_item.get("stock")
    if stock_val is not None and isinstance(stock_val, int):
        if source.business_type == BusinessType.B2B_WHOLESALE:
            return StockConfidence.HIGH
        return StockConfidence.MEDIUM

    # Check for available boolean
    if raw_item.get("available") is True:
        return StockConfidence.LOW

    return StockConfidence.UNKNOWN


# ══════════════════════════════════════════════════════════════════════════════
# AVAILABILITY DETECTION (B2B-First)
# ══════════════════════════════════════════════════════════════════════════════

def determine_availability(stock: Optional[int], stock_confidence: StockConfidence) -> AvailabilityState:
    """
    Determine availability from stock and confidence level.

    B2B-first logic:
    - stock > 0 → AVAILABLE_IN_STOCK
    - stock == 0 → OUT_OF_STOCK
    - stock is None → UNKNOWN (do NOT default to available)

    Stock must be explicitly verified. Price alone does NOT mean available.
    """
    if stock is not None:
        if stock > 0:
            return AvailabilityState.AVAILABLE_IN_STOCK
        else:
            return AvailabilityState.OUT_OF_STOCK
    # No stock data → UNKNOWN (never assume available)
    return AvailabilityState.UNKNOWN


def _detect_availability_deprecated(raw_listing: Dict[str, Any], price: Optional[Decimal]) -> AvailabilityState:
    """
    DEPRECATED: Legacy availability detection.
    Kept for reference. Use determine_availability() instead.

    Old logic defaulted to AVAILABLE_IN_STOCK when stock was None — WRONG.
    """
    available = raw_listing.get("available", True)
    stock = raw_listing.get("stock")

    if available is False or price is None or price <= 0:
        return AvailabilityState.OUT_OF_STOCK

    if stock is not None and stock <= 0:
        return AvailabilityState.OUT_OF_STOCK

    return AvailabilityState.AVAILABLE_IN_STOCK


# ══════════════════════════════════════════════════════════════════════════════
# VARIANT SIGNATURE
# ══════════════════════════════════════════════════════════════════════════════

def compute_variant_signature(volume_ml: Optional[int], fragrance_type: Optional[str]) -> Optional[str]:
    """
    Compute variant signature for a product listing.

    Used as part of the unique constraint (product_id, source_id, variant_signature)
    to distinguish different variants of the same product from the same source.

    Example: "100ml-EDP", "50ml-EDT", "200ml-PARFUM"
    """
    parts = []
    if volume_ml:
        parts.append(f"{volume_ml}ml")
    if fragrance_type:
        parts.append(fragrance_type.upper())

    if not parts:
        return None
    return "-".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# EAN-13 VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def _validate_ean(barcode: Any) -> Optional[str]:
    """
    Validate EAN-13/UPC barcode. Returns clean barcode or None.

    Rules:
    - None/empty → None (skip EAN lookup)
    - Non-digits → log warning, None
    - Wrong length → log warning, None
    - NEVER crashes the pipeline
    """
    if not barcode:
        return None

    clean = str(barcode).strip()
    if not clean:
        return None

    if not clean.isdigit():
        log.warning(f"Invalid EAN-13 (contains non-digits): '{barcode}'")
        return None

    if len(clean) not in (12, 13):
        log.warning(f"Invalid EAN-13 (length {len(clean)}, expected 12-13): '{barcode}'")
        return None

    return clean


# ══════════════════════════════════════════════════════════════════════════════
# DEDUPLICATION
# ══════════════════════════════════════════════════════════════════════════════

def _find_canonical_product(
    db: Session,
    ean_13: Optional[str],
    brand: str,
    product_name: str,
    volume_ml: Optional[int],
    variant: Optional[str],
) -> Optional[Product]:
    """
    Find an existing canonical product using the deduplication strategy.

    Priority:
    1. PRIMARY: EAN-13 exact match (if valid)
    2. SECONDARY: brand + product_name + volume_ml + variant (case-insensitive)
    """
    # PRIMARY: EAN-13 lookup
    if ean_13:
        product = db.query(Product).filter(Product.ean_13 == ean_13).first()
        if product:
            log.debug(f"Dedup match by EAN-13: {ean_13} → {product.product_code}")
            return product

    # SECONDARY: name-based lookup
    query = db.query(Product).filter(
        func.lower(Product.brand) == brand.lower(),
        func.lower(Product.product_name) == product_name.lower(),
    )

    if volume_ml is not None:
        query = query.filter(Product.volume_ml == volume_ml)
    else:
        query = query.filter(Product.volume_ml.is_(None))

    if variant:
        query = query.filter(func.lower(Product.variant) == variant.lower())
    else:
        query = query.filter(Product.variant.is_(None))

    product = query.first()
    if product:
        log.debug(f"Dedup match by name: {brand} {product_name} → {product.product_code}")

    return product


# ══════════════════════════════════════════════════════════════════════════════
# ENUM CONVERSION HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _to_fragrance_type(value: Optional[str]) -> Optional[FragranceType]:
    """Convert string to FragranceType enum, returning None on invalid values."""
    if not value:
        return None
    try:
        return FragranceType(value.upper())
    except ValueError:
        log.warning(f"Unknown fragrance type: '{value}'")
        return None


def _to_gender_type(value: Optional[str]) -> Optional[GenderType]:
    """Convert string to GenderType enum, returning None on invalid values."""
    if not value:
        return None
    try:
        return GenderType(value.upper())
    except ValueError:
        log.warning(f"Unknown gender type: '{value}'")
        return None


def _to_normalization_method(value: Optional[str]) -> Optional[NormalizationMethod]:
    """Convert string to NormalizationMethod enum."""
    if not value:
        return None
    try:
        return NormalizationMethod(value.upper())
    except ValueError:
        return NormalizationMethod.HYBRID


# ══════════════════════════════════════════════════════════════════════════════
# CORE DELTA PROCESSING
# ══════════════════════════════════════════════════════════════════════════════

class DeltaEngine:
    """
    Core update engine that processes raw listings and applies delta detection.

    Architecture: One Product, Many Listings
    - Each source gets its own ProductListing row
    - Multiple sources listing the same Product → multiple Listing rows
    - Comparison happens across listings, not by collapsing them

    All operations run within a single database transaction.
    """

    def __init__(self, db: Session):
        self.db = db
        self.records_updated = 0
        self.records_skipped = 0
        self.records_failed = 0

    def process_listing(self, raw_listing: Dict[str, Any], source_id: str) -> None:
        """
        Process a single raw listing through the delta pipeline.

        Steps:
        1. Parse price and extract stock using per-source-type strategy
        2. Determine stock confidence
        3. Determine availability (B2B-first: stock=None → UNKNOWN)
        4. Compute state hash (title EXCLUDED)
        5. Look up existing listing by source_id + source_external_id
        6. If hash matches → skip
        7. If hash differs → update listing + insert price_history
        8. If new → normalize, deduplicate, create product + listing

        Args:
            raw_listing: Dict with raw_title, price, stock, available, barcode, etc.
            source_id: UUID string of the source
        """
        raw_title = raw_listing.get("raw_title", "") or ""
        if not raw_title.strip():
            log.warning("Empty raw_title — skipping")
            self.records_failed += 1
            return

        try:
            # Look up source for B2B fields
            source = self.db.query(Source).filter(Source.id == source_id).first()
            source_type = source.engine_type.value if source else None

            # Parse price
            price_raw = raw_listing.get("price", 0.0)
            try:
                current_price = Decimal(str(price_raw)) if price_raw is not None else None
            except (InvalidOperation, ValueError):
                current_price = None

            # Extract stock using per-source-type strategy
            stock = extract_stock(raw_listing, source_type)

            # Determine stock confidence
            stock_confidence = determine_stock_confidence(raw_listing, source) if source else StockConfidence.UNKNOWN

            # Determine availability (B2B-first: stock=None → UNKNOWN)
            availability = determine_availability(stock, stock_confidence)

            # Compute state hash — title EXCLUDED
            current_hash = compute_state_hash(current_price, stock, availability.value)

            # Determine source_external_id
            sku = raw_listing.get("sku")
            listing_url = raw_listing.get("url", "") or ""
            source_external_id = sku if sku else _title_id(raw_title, listing_url)

            # Look up existing listing
            existing = self.db.query(ProductListing).filter(
                ProductListing.source_id == source_id,
                ProductListing.source_external_id == source_external_id,
            ).first()

            now = datetime.now(timezone.utc)

            if existing:
                if existing.current_hash == current_hash:
                    # Hash matches — no change, skip
                    existing.last_seen_at = now
                    existing.last_scraped_at = now
                    # Still update stock_confidence even if hash matches
                    existing.stock_confidence = stock_confidence
                    self.records_skipped += 1
                    return

                # Hash differs — update listing + record price history
                self._update_existing_listing(
                    existing, current_hash, current_price, stock, availability,
                    stock_confidence, raw_title, listing_url,
                    raw_listing.get("image_url"), now, source, raw_listing,
                )
                self.records_updated += 1
            else:
                # New listing — normalize, deduplicate, create
                self._create_new_listing(
                    raw_listing, source_id, source_external_id,
                    current_hash, current_price, stock, availability,
                    stock_confidence, raw_title, listing_url, now, source,
                )
                self.records_updated += 1

        except Exception as e:
            log.error(f"Failed to process listing '{raw_title[:60]}': {e}", exc_info=True)
            self.records_failed += 1

    def _update_existing_listing(
        self,
        listing: ProductListing,
        new_hash: str,
        price: Optional[Decimal],
        stock: Optional[int],
        availability: AvailabilityState,
        stock_confidence: StockConfidence,
        title: str,
        url: str,
        image_url: Optional[str],
        now: datetime,
        source: Optional[Source],
        raw_listing: Dict[str, Any],
    ) -> None:
        """Update an existing listing and insert a price history record — single transaction."""
        previous_price = listing.current_price
        previous_availability = listing.availability

        # Update listing state
        listing.current_hash = new_hash
        listing.current_price = price
        listing.current_stock = stock
        listing.title = title
        listing.stock_confidence = stock_confidence
        listing.last_seen_at = now
        listing.last_scraped_at = now

        # B2B wholesale fields
        if raw_listing.get("moq"):
            try:
                listing.moq = int(raw_listing["moq"])
            except (ValueError, TypeError):
                pass
        if raw_listing.get("bulk_tiers"):
            listing.bulk_pricing_tiers = raw_listing["bulk_tiers"]

        if url:
            listing.url = url
        if image_url:
            listing.image_url = image_url

        # Update last_confirmed_stock_at only when confidence is HIGH or MEDIUM
        if stock_confidence in (StockConfidence.HIGH, StockConfidence.MEDIUM):
            listing.last_confirmed_stock_at = now

        # Handle availability transition
        try:
            listing.transition_availability(availability, reason="scraped_update")
        except Exception as e:
            log.warning(f"Availability transition failed: {e}. Setting directly.")
            listing.availability = availability

        # Insert price history record if price or availability changed
        if price is not None and (previous_price != price or previous_availability != listing.availability):
            source_name = source.name if source else None
            history = PriceHistory(
                listing_id=listing.id,
                price=price,
                stock=stock,
                availability=listing.availability,
                source_name=source_name,
            )
            self.db.add(history)

        log.debug(f"Updated listing: {listing.source_external_id}")

    def _create_new_listing(
        self,
        raw_listing: Dict[str, Any],
        source_id: str,
        source_external_id: str,
        current_hash: str,
        price: Optional[Decimal],
        stock: Optional[int],
        availability: AvailabilityState,
        stock_confidence: StockConfidence,
        raw_title: str,
        url: str,
        now: datetime,
        source: Optional[Source],
    ) -> None:
        """Normalize, deduplicate, and create a new product listing."""
        # Normalize the product
        vendor = raw_listing.get("vendor")
        tags = raw_listing.get("tags", [])
        barcode = raw_listing.get("barcode")
        description = raw_listing.get("description", "")

        normalized = normalize_product(raw_title, vendor, tags, barcode, description)

        # Validate critical fields
        if not normalized.volume_ml or not normalized.fragrance_type:
            log.info(f"Skipped non-perfume item (missing volume_ml or fragrance_type): {raw_title[:80]}")
            self.records_failed += 1
            return

        # Validate and clean EAN
        ean_13 = _validate_ean(barcode)

        # Deduplicate
        canonical = _find_canonical_product(
            self.db,
            ean_13=ean_13,
            brand=normalized.brand,
            product_name=normalized.product_name,
            volume_ml=normalized.volume_ml,
            variant=normalized.variant,
        )

        if not canonical:
            # Create new product with auto-generated code
            canonical = Product.create_with_code(
                self.db,
                brand=normalized.brand,
                product_name=normalized.product_name,
                variant=normalized.variant,
                fragrance_type=_to_fragrance_type(normalized.fragrance_type),
                volume_ml=normalized.volume_ml,
                gender=_to_gender_type(normalized.gender),
                ean_13=ean_13,
                normalization_method=_to_normalization_method(normalized.normalization_method),
                confidence_score=Decimal(str(normalized.confidence_score)),
                normalized_name=normalized.product_name.lower().strip(),
                category=infer_category(normalized.brand, tags),
            )
            self.db.flush()  # Get product.id without full commit
            log.info(f"Created product {canonical.product_code}: {normalized.brand} {normalized.product_name}")
        else:
            # Enrich existing product with newly discovered fields
            if not canonical.fragrance_type and normalized.fragrance_type:
                canonical.fragrance_type = _to_fragrance_type(normalized.fragrance_type)
            if not canonical.volume_ml and normalized.volume_ml:
                canonical.volume_ml = normalized.volume_ml
            if not canonical.gender and normalized.gender:
                canonical.gender = _to_gender_type(normalized.gender)
            if not canonical.ean_13 and ean_13:
                canonical.ean_13 = ean_13
            if not canonical.normalized_name:
                canonical.normalized_name = normalized.product_name.lower().strip()

        # Compute variant signature
        variant_signature = compute_variant_signature(
            normalized.volume_ml,
            normalized.fragrance_type,
        )

        # Check for existing listing with same (product_id, source_id, variant_signature)
        existing_by_variant = None
        if variant_signature:
            existing_by_variant = self.db.query(ProductListing).filter(
                ProductListing.product_id == canonical.id,
                ProductListing.source_id == source_id,
                ProductListing.variant_signature == variant_signature,
            ).first()

        if existing_by_variant:
            # Update the existing listing found by variant signature
            self._update_existing_listing(
                existing_by_variant, current_hash, price, stock, availability,
                stock_confidence, raw_title, url,
                raw_listing.get("image_url"), now, source, raw_listing,
            )
            log.info(f"Merged listing by variant_signature: {variant_signature}")
            return

        # Create listing
        listing = ProductListing(
            product_id=canonical.id,
            source_id=source_id,
            source_external_id=source_external_id,
            title=raw_title,
            url=url,
            image_url=raw_listing.get("image_url"),
            current_hash=current_hash,
            current_price=price,
            current_stock=stock,
            availability=availability,
            stock_confidence=stock_confidence,
            variant_signature=variant_signature,
            currency=source.currency if source else "CLP",
            last_seen_at=now,
            last_scraped_at=now,
        )

        # B2B wholesale fields
        if raw_listing.get("moq"):
            try:
                listing.moq = int(raw_listing["moq"])
            except (ValueError, TypeError):
                pass
        if raw_listing.get("bulk_tiers"):
            listing.bulk_pricing_tiers = raw_listing["bulk_tiers"]

        # Update last_confirmed_stock_at only when confidence is HIGH or MEDIUM
        if stock_confidence in (StockConfidence.HIGH, StockConfidence.MEDIUM):
            listing.last_confirmed_stock_at = now

        self.db.add(listing)
        self.db.flush()

        # Insert initial price history
        if price is not None:
            source_name = source.name if source else None
            history = PriceHistory(
                listing_id=listing.id,
                price=price,
                stock=stock,
                availability=availability,
                source_name=source_name,
            )
            self.db.add(history)

        log.info(
            f"Created listing: [{normalized.brand}] {normalized.product_name} "
            f"{normalized.volume_ml}ml ({normalized.fragrance_type}) @ {price}"
        )

    def process_batch(self, raw_listings: List[Dict[str, Any]], source_id: str) -> Dict[str, int]:
        """
        Process a batch of raw listings through the delta pipeline.

        All operations within a single transaction.

        Args:
            raw_listings: List of raw listing dicts
            source_id: UUID string of the source

        Returns:
            Dict with records_updated, records_skipped, records_failed counts
        """
        self.records_updated = 0
        self.records_skipped = 0
        self.records_failed = 0

        for listing in raw_listings:
            try:
                self.process_listing(listing, source_id)
            except Exception as e:
                log.error(f"Batch item failed: {e}", exc_info=True)
                self.records_failed += 1

        # Single commit for the entire batch
        try:
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            log.error(f"Batch commit failed: {e}", exc_info=True)
            raise DatabaseError(f"Batch commit failed: {e}")

        return {
            "records_updated": self.records_updated,
            "records_skipped": self.records_skipped,
            "records_failed": self.records_failed,
        }


# ══════════════════════════════════════════════════════════════════════════════
# CATEGORY INFERENCE
# ══════════════════════════════════════════════════════════════════════════════

# Known niche brands (non-exhaustive, grows over time)
NICHE_BRANDS = {
    "creed", "tom ford", "byredo", "le labo", "diptyque", " Frederic Malle",
    "amouage", "mancera", "montale", "parfums de marly", "nishane",
    "xerjoff", "roja", "arcana", "nasomatto", "orto parisii",
    "bruno acampora", "profumum roma", "tauer", "serge lutens",
    "l'artisan parfumeur", "penhaligon's", "bdk parfums", "initio",
}

DESIGNER_BRANDS = {
    "dior", "chanel", "gucci", "versace", "armani", "ysl", "saint laurent",
    "hermes", "bvlgari", "prada", "valentino", "burberry", "dolce & gabbana",
    "calvin klein", "ralph lauren", "hugo boss", "lacoste", "montblanc",
    "givenchy", "kenzo", "lanvin", "paco rabanne", "jean paul gaultier",
}

ARABIC_BRANDS = {
    "armaf", "rasasi", "al haramain", "lattafa", "swiss arabian",
    "ajmal", "maison alhambra", "ester bracket", "adir",
}


def infer_category(brand: str, tags: List[str] = None) -> Optional[str]:
    """
    Infer product category from brand name and tags.

    Returns: 'niche', 'designer', 'celebrity', 'arabic', or None
    """
    brand_lower = brand.lower().strip()

    if brand_lower in NICHE_BRANDS:
        return "niche"
    if brand_lower in ARABIC_BRANDS:
        return "arabic"
    if brand_lower in DESIGNER_BRANDS:
        return "designer"

    # Check tags
    if tags:
        tag_lower = [t.lower() for t in tags]
        if any(t in tag_lower for t in ("niche", "exclusive", "prestige")):
            return "niche"
        if any(t in tag_lower for t in ("arabic", "middle eastern", "oriental")):
            return "arabic"
        if any(t in tag_lower for t in ("celebrity",)):
            return "celebrity"

    return "designer" if brand_lower else None


# ══════════════════════════════════════════════════════════════════════════════
# DELISTED TRANSITION
# ══════════════════════════════════════════════════════════════════════════════

def transition_delisted(db: Session, source_id: str, threshold_minutes: int = 1440) -> int:
    """
    Mark listings as DELISTED if not seen for longer than the threshold.

    Products that have not been seen by the scraper within the threshold
    window are assumed to have been removed from the source.

    Args:
        db: SQLAlchemy session
        source_id: UUID string of the source
        threshold_minutes: Minutes of absence before marking as DELISTED (default: 24h)

    Returns:
        Number of listings transitioned to DELISTED
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)

    stale_listings = db.query(ProductListing).filter(
        ProductListing.source_id == source_id,
        ProductListing.last_seen_at < cutoff,
        ProductListing.availability != AvailabilityState.DELISTED,
    ).all()

    count = 0
    for listing in stale_listings:
        try:
            listing.transition_availability(AvailabilityState.DELISTED, reason="stale_delist")
            count += 1
            log.info(f"Delisted: {listing.source_external_id} (last seen: {listing.last_seen_at})")
        except Exception as e:
            log.warning(f"Could not delist {listing.source_external_id}: {e}")

    if count > 0:
        db.commit()
        log.info(f"Transitioned {count} listings to DELISTED for source {source_id}")

    return count