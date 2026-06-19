"""
Run the Cosmetic Distribucion scraper and display all extracted products.
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import asyncio
import json
import csv
import os
from app.database import SessionLocal
from app.models import Source
from scrapers.factory import ScraperFactory

def main():
    db = SessionLocal()
    source = db.query(Source).filter(Source.name.ilike("%pdl bodega%")).first()
    if not source:
        print("Source 'PDL Bodega' not found in database.")
        return

    print(f"{'='*80}")
    print(f"  Source: {source.name}")
    print(f"  URL: {source.base_url}")
    print(f"  Engine: {source.engine_type}")
    print(f"{'='*80}\n")

    scraper = ScraperFactory.get_scraper(
        source.engine_type, source.base_url, str(source.id), source.config
    )

    print("Starting extraction (this may take a few minutes)...\n")
    catalog = asyncio.run(scraper.extract_catalog())

    if not catalog:
        print("No products extracted.")
        return

    # Display summary table
    print(f"\n{'='*80}")
    print(f"  TOTAL PRODUCTS EXTRACTED: {len(catalog)}")
    print(f"{'='*80}\n")

    # Print header
    print(f"{'#':>4}  {'Brand':<25} {'SKU':<12} {'Product Name':<45} {'Price':>10} {'Stock':>6}")
    print(f"{'-'*4}  {'-'*25} {'-'*12} {'-'*45} {'-'*10} {'-'*6}")

    brands = set()
    total_with_price = 0
    total_with_image = 0

    for i, item in enumerate(catalog, 1):
        brand = (item.get("vendor") or "")[:25]
        sku = (item.get("sku") or "")[:12]
        title = (item.get("raw_title") or "")[:45]
        price = item.get("price", 0)
        stock = item.get("stock")
        stock_str = str(stock) if stock is not None else "-"
        price_str = f"${price:,.0f}" if price else "-"

        if brand:
            brands.add(brand)
        if price:
            total_with_price += 1
        if item.get("image_url"):
            total_with_image += 1

        print(f"{i:>4}  {brand:<25} {sku:<12} {title:<45} {price_str:>10} {stock_str:>6}")

    # Save to CSV
    csv_path = "pdl_products.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "raw_title", "vendor", "sku", "barcode", "price",
            "description", "url", "image_url", "tags", "stock", "available"
        ])
        writer.writeheader()
        writer.writerows(catalog)

    # Save to JSON
    json_path = "pdl_products.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    # Summary
    print(f"\n{'='*80}")
    print(f"  SUMMARY")
    print(f"{'='*80}")
    print(f"  Total products:     {len(catalog)}")
    print(f"  Unique brands:      {len(brands)}")
    print(f"  With price:         {total_with_price}")
    print(f"  With image:         {total_with_image}")
    print(f"  Saved to CSV:       {os.path.abspath(csv_path)}")
    print(f"  Saved to JSON:      {os.path.abspath(json_path)}")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
