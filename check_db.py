"""Check existing tables and apply new schema if needed."""
from sqlalchemy import text
from app.database import engine

# Check what tables currently exist
with engine.connect() as conn:
    result = conn.execute(text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY table_name"
    ))
    tables = [row[0] for row in result]
    print(f"Current tables ({len(tables)}):", tables)

    # Check if new schema tables exist
    new_tables = {'sources', 'products', 'product_listings', 'price_history',
                  'price_tiers', 'scrape_logs', 'scrape_queue'}
    old_tables = {'sources', 'products', 'product_listings', 'price_history', 'scrape_logs'}

    existing = set(tables)
    missing_new = new_tables - existing
    if missing_new:
        print(f"\nMissing new schema tables: {missing_new}")
        print("Need to apply schema.sql")
    else:
        print("\nAll new schema tables exist")

    # Check enums
    result = conn.execute(text(
        "SELECT typname FROM pg_type WHERE typtype = 'e' ORDER BY typname"
    ))
    enums = [row[0] for row in result]
    print(f"\nExisting enums: {enums}")

    new_enums = {'availability_state', 'engine_type', 'scrape_status',
                 'normalization_method', 'gender_type', 'fragrance_type'}
    missing_enums = new_enums - set(enums)
    if missing_enums:
        print(f"Missing enums: {missing_enums}")
