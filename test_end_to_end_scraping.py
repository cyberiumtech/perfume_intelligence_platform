import asyncio
import uuid
import logging
from app.database import SessionLocal
from app.models import Source, EngineType, AvailabilityState
from app.scrapers.shopify import ShopifyScraper
from app.normalization import get_normalizer

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("test_scrape")

async def test_end_to_end():
    db = SessionLocal()
    
    # 1. Create a test source in the DB
    base_url = "https://www.silkperfumes.cl"
    source = db.query(Source).filter(Source.base_url == base_url).first()
    if not source:
        source = Source(
            id=uuid.uuid4(),
            name="Silk Perfumes",
            base_url=base_url,
            engine_type=EngineType.SHOPIFY,
            config={"max_pages": 1},
            currency="CLP"
        )
        db.add(source)
        db.commit()
        db.refresh(source)
    
    log.info(f"Using Source: {source.name} ({source.id})")

    # 2. Instantiate the Scraper
    scraper = ShopifyScraper(source.base_url, str(source.id), source.config)
    
    # 3. Scrape data
    log.info("Starting scrape...")
    results = await scraper.extract_catalog()
    log.info(f"Scraped {len(results)} raw items from {base_url}")
    
    if not results:
        log.error("No results returned. Exiting.")
        return

    # 4. Normalize and print a few samples
    normalizer = get_normalizer("bedrock")  # Will fallback to regex locally
    
    print("\n--- SAMPLE NORMALIZED PRODUCTS ---")
    for i, raw in enumerate(results[:5]):
        norm = normalizer.normalize(raw['raw_title'], raw['vendor'], raw['tags'], raw['barcode'])
        print(f"\nRAW TITLE: {raw['raw_title']}")
        print(f"BRAND: {norm.brand}")
        print(f"PRODUCT: {norm.product_name}")
        print(f"TYPE: {norm.fragrance_type} | ML: {norm.volume_ml} | GENDER: {norm.gender}")
        print(f"PRICE: {raw['price']} CLP | STOCK: {raw['stock']}")

    db.close()

if __name__ == "__main__":
    asyncio.run(test_end_to_end())
