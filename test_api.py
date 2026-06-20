"""Full API integration test with the real database."""
from fastapi.testclient import TestClient
from app.main import app
import json

client = TestClient(app, raise_server_exceptions=False)

print("=== API INTEGRATION TEST (with DB) ===")
print()

# 1. Health
r = client.get("/api/v1/health")
print(f"1. GET /api/v1/health -> {r.status_code}: {r.json()}")
assert r.status_code == 200
assert r.json()["database"] == "connected"

# 2. Sources (empty)
r = client.get("/api/v1/sources")
print(f"2. GET /api/v1/sources -> {r.status_code}: {r.json()}")
assert r.status_code == 200
assert r.json() == []

# 3. Create a source
r = client.post("/api/v1/sources", json={
    "name": "Test Shopify Store",
    "base_url": "https://teststore.cl",
    "engine_type": "shopify",
    "config": {"max_pages": 5},
})
print(f"3. POST /api/v1/sources -> {r.status_code}: {r.json()}")
assert r.status_code == 201
source = r.json()
source_id = source["id"]
assert source["name"] == "Test Shopify Store"
assert source["currency"] == "CLP"

# 4. List sources (now 1)
r = client.get("/api/v1/sources")
print(f"4. GET /api/v1/sources -> {r.status_code}: {len(r.json())} source(s)")
assert r.status_code == 200
assert len(r.json()) == 1

# 5. Duplicate source → 409
r = client.post("/api/v1/sources", json={
    "name": "Test Shopify Store",
    "base_url": "https://teststore.cl",
    "engine_type": "shopify",
})
print(f"5. POST /api/v1/sources (dup) -> {r.status_code}")
assert r.status_code == 409

# 6. Products (empty)
r = client.get("/api/v1/products")
print(f"6. GET /api/v1/products -> {r.status_code}: {r.json()}")
assert r.status_code == 200
assert r.json() == []

# 7. Products with filter (empty)
r = client.get("/api/v1/products?brand=Dior&ml=100")
print(f"7. GET /api/v1/products?brand=Dior&ml=100 -> {r.status_code}: {r.json()}")
assert r.status_code == 200

# 8. Product by code → 404
r = client.get("/api/v1/products/a00000001")
print(f"8. GET /api/v1/products/a00000001 -> {r.status_code}")
assert r.status_code == 404

# 9. Scrape logs (empty)
r = client.get("/api/v1/scrape-logs")
print(f"9. GET /api/v1/scrape-logs -> {r.status_code}: {r.json()}")
assert r.status_code == 200
assert r.json() == []

# 10. Trigger scrape
r = client.post("/api/v1/trigger-scrape", json={
    "source_id": source_id,
    "priority": 8,
})
print(f"10. POST /api/v1/trigger-scrape -> {r.status_code}: {r.json()}")
assert r.status_code == 200
assert "queue_id" in r.json()

# 11. Trigger scrape with non-existent source → 404
r = client.post("/api/v1/trigger-scrape", json={
    "source_id": "00000000-0000-0000-0000-000000000000",
    "priority": 5,
})
print(f"11. POST /api/v1/trigger-scrape (bad source) -> {r.status_code}")
assert r.status_code == 404

# 12. Price comparison → 404 (no products)
r = client.get("/api/v1/price-comparison/a00000001")
print(f"12. GET /api/v1/price-comparison/a00000001 -> {r.status_code}")
assert r.status_code == 404

# 13. OpenAPI spec
r = client.get("/openapi.json")
print(f"13. GET /openapi.json -> {r.status_code}")
assert r.status_code == 200
paths = list(r.json()["paths"].keys())
assert len(paths) == 7

# 14. Swagger docs
r = client.get("/docs")
print(f"14. GET /docs -> {r.status_code}")
assert r.status_code == 200

# ── Cleanup: remove test source ──
from app.database import SessionLocal
from app.models import Source, ScrapeQueue
db = SessionLocal()
db.query(ScrapeQueue).filter(ScrapeQueue.source_id == source_id).delete()
db.query(Source).filter(Source.name == "Test Shopify Store").delete()
db.commit()
db.close()
print("\n[CLEANUP] Removed test source and queue entries")

print()
print("=" * 60)
print("  ALL 14 API INTEGRATION TESTS PASSED")
print("=" * 60)
