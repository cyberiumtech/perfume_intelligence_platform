"""Inline migration: add missing columns to existing tables without dropping data."""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from app.database import engine
from sqlalchemy import text

MIGRATIONS = [
    "ALTER TABLE sources ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS gender VARCHAR(6)",
    "ALTER TABLE product_listings ADD COLUMN IF NOT EXISTS url TEXT",
    "ALTER TABLE product_listings ADD COLUMN IF NOT EXISTS image_url TEXT",
    "ALTER TABLE product_listings ADD COLUMN IF NOT EXISTS is_available BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE scrape_logs ADD COLUMN IF NOT EXISTS records_updated INTEGER DEFAULT 0",
    "ALTER TABLE scrape_logs ADD COLUMN IF NOT EXISTS records_skipped INTEGER DEFAULT 0",
]

with engine.connect() as conn:
    for sql in MIGRATIONS:
        try:
            conn.execute(text(sql))
            print(f"OK: {sql[:60]}")
        except Exception as e:
            print(f"SKIP (already exists): {e}")
    conn.commit()

print("Migration complete.")
