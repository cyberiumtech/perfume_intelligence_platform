"""Drop all existing objects and re-apply schema cleanly."""
import psycopg2

conn = psycopg2.connect("postgresql://postgres:root@localhost:5432/perfume_intelligence_db")
conn.autocommit = True
cur = conn.cursor()

# Drop everything
print("Dropping all existing objects...")
drop_statements = [
    "DROP TABLE IF EXISTS scrape_queue CASCADE",
    "DROP TABLE IF EXISTS scrape_logs CASCADE",
    "DROP TABLE IF EXISTS price_tiers CASCADE",
    "DROP TABLE IF EXISTS price_history CASCADE",
    "DROP TABLE IF EXISTS product_listings CASCADE",
    "DROP TABLE IF EXISTS products CASCADE",
    "DROP TABLE IF EXISTS sources CASCADE",
    "DROP FUNCTION IF EXISTS get_price_comparison(VARCHAR) CASCADE",
    "DROP FUNCTION IF EXISTS products_search_vector_update() CASCADE",
    "DROP FUNCTION IF EXISTS update_updated_at_column() CASCADE",
    "DROP TYPE IF EXISTS availability_state CASCADE",
    "DROP TYPE IF EXISTS engine_type CASCADE",
    "DROP TYPE IF EXISTS scrape_status CASCADE",
    "DROP TYPE IF EXISTS normalization_method CASCADE",
    "DROP TYPE IF EXISTS gender_type CASCADE",
    "DROP TYPE IF EXISTS fragrance_type CASCADE",
]

for stmt in drop_statements:
    try:
        cur.execute(stmt)
        print(f"  OK: {stmt[:60]}")
    except Exception as e:
        print(f"  SKIP: {str(e)[:80]}")

# Now apply schema.sql
print("\nApplying schema.sql...")
with open("schema.sql", "r", encoding="utf-8") as f:
    schema_sql = f.read()

try:
    cur.execute(schema_sql)
    print("[OK] Schema applied successfully!")
except Exception as e:
    print(f"[ERROR] {e}")

cur.close()
conn.close()

# Verify
from sqlalchemy import text
from app.database import engine

with engine.connect() as conn:
    result = conn.execute(text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY table_name"
    ))
    tables = [row[0] for row in result]
    print(f"\nTables ({len(tables)}): {tables}")

    result = conn.execute(text(
        "SELECT typname FROM pg_type WHERE typtype = 'e' ORDER BY typname"
    ))
    enums = [row[0] for row in result]
    print(f"Enums ({len(enums)}): {enums}")

    result = conn.execute(text(
        "SELECT COUNT(*) FROM pg_indexes WHERE schemaname = 'public'"
    ))
    print(f"Indexes: {result.fetchone()[0]}")

    result = conn.execute(text(
        "SELECT routine_name FROM information_schema.routines "
        "WHERE routine_schema = 'public' ORDER BY routine_name"
    ))
    print(f"Functions: {[row[0] for row in result]}")

    result = conn.execute(text(
        "SELECT trigger_name FROM information_schema.triggers "
        "WHERE trigger_schema = 'public' ORDER BY trigger_name"
    ))
    print(f"Triggers: {[row[0] for row in result]}")
