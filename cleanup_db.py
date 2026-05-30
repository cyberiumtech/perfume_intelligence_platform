import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app.database import SessionLocal
from app.models import Source, Product, ProductListing, PriceHistory, ScrapeLog

def clean_database():
    db = SessionLocal()
    
    # 1. Delete the dummy ColourPop source and its associated data
    colourpop_sources = db.query(Source).filter(Source.name.ilike("%ColourPop%")).all()
    for s in colourpop_sources:
        print(f"Deleting test source: {s.name}")
        db.query(ScrapeLog).filter(ScrapeLog.source_id == s.id).delete()
        
        listings = db.query(ProductListing).filter(ProductListing.source_id == s.id).all()
        for listing in listings:
            db.query(PriceHistory).filter(PriceHistory.listing_id == listing.id).delete()
            db.delete(listing)
        db.delete(s)
        db.commit()

    # 2. Find all products that have NULL ml or NULL fragrance_type
    invalid_products = db.query(Product).filter(
        (Product.ml == None) | (Product.fragrance_type == None)
    ).all()
    
    print(f"Found {len(invalid_products)} non-perfume products (missing ml or fragrance_type). Deleting...")
    
    deleted_count = 0
    for product in invalid_products:
        # Delete related listings and their price histories
        listings = db.query(ProductListing).filter(ProductListing.product_id == product.id).all()
        for listing in listings:
            db.query(PriceHistory).filter(PriceHistory.listing_id == listing.id).delete()
            db.delete(listing)
        # Delete the product itself
        db.delete(product)
        deleted_count += 1
        
    db.commit()
    print(f"Successfully deleted {deleted_count} non-perfume products from the database.")
    
    db.close()

if __name__ == "__main__":
    clean_database()
