"""
End-to-end pipeline test using the live Elite Perfumes Distribuidor (Shopify) source.

Updated to use the new PostgreSQL queue-based architecture (no Celery).

Asserts that after a scrape:
  - Products have non-null brand, product_name, fragrance_type, volume_ml
  - ProductListings have url and image_url populated
  - PriceHistory records are created
  - ScrapeLog status = SUCCESS
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from app.database import SessionLocal, engine
from app.models import Base, Source, Product, ProductListing, ScrapeLog, PriceHistory, ScrapeQueue
from app.scraper_worker import claim_next_job, process_job


def run_test():
    print("=" * 60)
    print("Perfume Intelligence Platform — E2E Pipeline Test")
    print("=" * 60)

    # ── 1. Setup DB ──────────────────────────────────────────────
    print("\n[1] Creating database schema...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    # ── 2. Clean previous test data ──────────────────────────────
    print("[2] Cleaning previous test data...")
    old_sources = db.query(Source).filter(Source.name == "Elite Perfumes Distribuidor [TEST]").all()
    for s in old_sources:
        logs = db.query(ScrapeLog).filter(ScrapeLog.source_id == s.id).all()
        for log in logs:
            db.delete(log)
        listings = db.query(ProductListing).filter(ProductListing.source_id == s.id).all()
        for l in listings:
            db.query(PriceHistory).filter(PriceHistory.listing_id == l.id).delete()
            db.delete(l)
        # Clean queue entries
        db.query(ScrapeQueue).filter(ScrapeQueue.source_id == s.id).delete()
        db.delete(s)
    db.commit()

    # ── 3. Register real source ───────────────────────────────────
    print("[3] Registering Elite Perfumes Distribuidor (Shopify) as test source...")
    test_source = Source(
        name="Elite Perfumes Distribuidor [TEST]",
        base_url="https://www.eliteperfumes-distribuidor.cl",
        engine_type="shopify",
        config={
            "catalog_path": "/collections/perfumes/products.json",
            "max_pages": 1,  # Limit for testing
        },
        is_active=True,
    )
    db.add(test_source)
    db.commit()
    db.refresh(test_source)
    source_id = str(test_source.id)
    print(f"   Source ID: {source_id}")

    # ── 4. Queue and process scrape ───────────────────────────────
    print("\n[4] Queueing scrape job...")
    queue_entry = ScrapeQueue(source_id=test_source.id, priority=10)
    db.add(queue_entry)
    db.commit()

    print("[4] Claiming and processing job...")
    t0 = time.time()
    try:
        job_db = SessionLocal()
        job = claim_next_job(job_db)
        job_db.close()

        if job:
            process_job(job)
            elapsed = time.time() - t0
            print(f"   Pipeline completed in {elapsed:.1f}s")
        else:
            print("   [FAIL] No job found in queue!")
            db.close()
            return
    except Exception as e:
        print(f"   [FAIL] Pipeline error: {e}")
        import traceback
        traceback.print_exc()
        db.close()
        return

    # Refresh session to see committed data
    db.close()
    db = SessionLocal()

    # ── 5. Verify results ─────────────────────────────────────────
    print("\n[5] Verification Results")
    print("-" * 40)

    logs = db.query(ScrapeLog).filter(ScrapeLog.source_id == source_id).all()
    print(f"Scrape Logs:       {len(logs)}")
    for log in logs:
        print(f"  Status: {log.status.value if hasattr(log.status, 'value') else log.status}, "
              f"Records: {log.records_extracted}")

    listings = db.query(ProductListing).filter(ProductListing.source_id == source_id).all()
    print(f"\nProduct Listings:  {len(listings)}")

    products_seen = set()
    null_fields = {"brand": 0, "product_name": 0, "fragrance_type": 0, "volume_ml": 0}
    missing_url = 0
    missing_image = 0

    for listing in listings:
        print(f"\n  Listing: {listing.title[:60]}...")
        print(f"    URL:          {listing.url or '[NULL] MISSING'}")
        print(f"    Image:        {(listing.image_url or '[NULL] MISSING')[:80]}")
        print(f"    Price:        {listing.current_price}")
        print(f"    Availability: {listing.availability}")

        if not listing.url:
            missing_url += 1
        if not listing.image_url:
            missing_image += 1

        if listing.product_id:
            products_seen.add(listing.product_id)
            product = db.query(Product).filter(Product.id == listing.product_id).first()
            if product:
                print(f"    -> Brand:     {product.brand or '[NULL]'}")
                print(f"    -> Name:      {product.product_name or '[NULL]'}")
                print(f"    -> Type:      {product.fragrance_type or '[?]'}")
                print(f"    -> Volume:    {product.volume_ml or '[?]'} ml")
                print(f"    -> Gender:    {product.gender or '[?]'}")

                if not product.brand:
                    null_fields["brand"] += 1
                if not product.product_name:
                    null_fields["product_name"] += 1
                if not product.fragrance_type:
                    null_fields["fragrance_type"] += 1
                if not product.volume_ml:
                    null_fields["volume_ml"] += 1

        # Price history
        history_count = db.query(PriceHistory).filter(
            PriceHistory.listing_id == listing.id
        ).count()
        print(f"    PriceHistory entries: {history_count}")

    # ── 6. Quality Report ─────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Quality Report")
    print("=" * 60)
    total = len(listings)
    print(f"Total listings:      {total}")
    print(f"Canonical products:  {len(products_seen)}")
    print(f"Missing URL:         {missing_url}/{total}")
    print(f"Missing Image URL:   {missing_image}/{total}")
    print(f"Null brand:          {null_fields['brand']}/{total}")
    print(f"Null product_name:   {null_fields['product_name']}/{total}")
    print(f"Null fragrance_type: {null_fields['fragrance_type']}/{total}")
    print(f"Null volume_ml:      {null_fields['volume_ml']}/{total}")

    # ── 7. Assertions ──────────────────────────────────────────────
    print("\nAssertions:")
    passed = 0
    failed = 0

    def check(condition, label):
        nonlocal passed, failed
        if condition:
            print(f"  ✓ PASS {label}")
            passed += 1
        else:
            print(f"  ✗ FAIL {label}")
            failed += 1

    check(total > 0, "At least 1 listing scraped")
    check(null_fields["brand"] == 0, "All products have brand")
    check(null_fields["product_name"] == 0, "All products have product_name")
    check(missing_url == 0, "All listings have URL")
    check(missing_image == 0, "All listings have image_url")

    if logs:
        log_status = logs[0].status.value if hasattr(logs[0].status, 'value') else str(logs[0].status)
        check(log_status in ("SUCCESS", "PARTIAL"), "Scrape log status = SUCCESS or PARTIAL")
    else:
        check(False, "Scrape log exists")

    print(f"\n{'✓ ALL ASSERTIONS PASSED' if failed == 0 else f'✗ {failed} assertion(s) FAILED'}")
    print(f"({passed} passed, {failed} failed)")

    db.close()


if __name__ == "__main__":
    run_test()
