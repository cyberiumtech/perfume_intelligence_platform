# Perfume Intelligence Platform — Architecture Documentation

## Overview

A B2B wholesale intelligence platform for Chilean perfume retailers. The platform scrapes B2B wholesale distributor portals and provides a unified comparison layer: for any given canonical product, show all distributor listings with price, stock, source reliability, and MOQ side-by-side.

**Key principle:** Retailers are anonymous query users ONLY — no retailer data, accounts, or pricing lives in this database.

---

## Core Architecture Principles

### 1. One Product, Many Listings

The fundamental architectural pattern:

- **`products` table**: Holds canonical identity ONLY (brand, name, volume, concentration)
- **`product_listings` table**: One row PER source PER product variant
- **Comparison happens across listings**, not by collapsing them

**Example:** If "Dior Sauvage 100ml EDP" is scraped from 3 distributors:
- ✅ Creates **1 Product** + **3 ProductListings**
- ❌ Does NOT create 3 separate products
- ❌ Does NOT create 1 product with 1 listing (overwriting)

### 2. Stock is Per-Listing

Every source's stock count is independently tracked:
- Distributor A may have 15 units
- Distributor B may be out of stock (0 units)
- Distributor C may not report stock (NULL → UNKNOWN)

### 3. Price History is Per-Listing

Each `ProductListing` has its own `price_history` time-series that records:
- Price changes
- Stock changes
- Availability state transitions
- Source name (denormalized for fast querying)

### 4. B2B-First Schema

MOQ (Minimum Order Quantity), bulk pricing tiers, and stock confidence are first-class fields:
- `moq`: Minimum order quantity
- `bulk_pricing_tiers`: JSON array of `[{"qty": 10, "price": 40.0}, ...]`
- `stock_confidence`: `HIGH` | `MEDIUM` | `LOW` | `UNKNOWN`
- `last_confirmed_stock_at`: Timestamp of last HIGH/MEDIUM confidence stock update

### 5. Fail Loud

Empty scrapes, login failures, and stock extraction failures MUST be logged as non-success states:
- `EMPTY`: Scraper ran but found 0 products
- `FAILED_AUTH`: Login credentials failed
- `FAILED_BLOCKED`: IP/rate limit block
- `FAILED_ERROR`: Scraper crashed

---

## Database Schema

### Core Tables

#### `products` — Canonical Product Identity

```sql
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_code VARCHAR(16) UNIQUE NOT NULL,  -- Auto-generated: cyb00000001
    ean_13 VARCHAR(13),
    brand VARCHAR(255) NOT NULL,
    product_name VARCHAR(255) NOT NULL,
    variant VARCHAR(255),
    fragrance_type fragrance_type,  -- EDP, EDT, PARFUM, etc.
    volume_ml INTEGER,
    gender gender_type,  -- M, F, UNISEX
    normalized_name VARCHAR(255),  -- Lowercase for deduplication
    category VARCHAR(100),  -- niche, designer, celebrity, arabic
    suggested_retail_price NUMERIC(12, 2),
    confidence_score NUMERIC(3, 2),
    normalization_method normalization_method,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(brand, product_name, variant, volume_ml)
);
```

#### `product_listings` — Per-Source Listings

```sql
CREATE TABLE product_listings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    source_id UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    source_external_id VARCHAR(255) NOT NULL,  -- SKU or hash(title+url)
    
    title VARCHAR(500) NOT NULL,
    url VARCHAR(1024),
    image_url VARCHAR(1024),
    
    current_hash VARCHAR(64) NOT NULL,  -- SHA256(price | stock | availability)
    current_price NUMERIC(12, 2),
    current_stock INTEGER,
    currency VARCHAR(3) NOT NULL DEFAULT 'CLP',
    availability availability_state NOT NULL DEFAULT 'UNKNOWN',
    
    -- B2B wholesale fields
    moq INTEGER,  -- Minimum Order Quantity
    bulk_pricing_tiers JSONB DEFAULT '[]',
    stock_confidence stock_confidence NOT NULL DEFAULT 'UNKNOWN',
    last_confirmed_stock_at TIMESTAMPTZ,
    is_discontinued BOOLEAN NOT NULL DEFAULT false,
    variant_signature VARCHAR(255),  -- e.g., "100ml-EDP-Boxed"
    
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_scraped_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    UNIQUE(source_id, source_external_id),
    UNIQUE(product_id, source_id, variant_signature)
);
```

#### `price_history` — Time-Series Price & Availability

```sql
CREATE TABLE price_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id UUID NOT NULL REFERENCES product_listings(id) ON DELETE CASCADE,
    price NUMERIC(12, 2) NOT NULL,
    stock INTEGER,
    availability availability_state NOT NULL,
    source_name VARCHAR(255),  -- Denormalized for fast querying
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### `sources` — Scraping Sources

```sql
CREATE TABLE sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) UNIQUE NOT NULL,
    base_url VARCHAR(512) NOT NULL,
    engine_type engine_type NOT NULL,  -- shopify, bs4_woocommerce, playwright
    config JSONB NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT true,
    currency VARCHAR(3) NOT NULL DEFAULT 'CLP',
    
    -- B2B wholesale fields
    business_type business_type DEFAULT 'B2B_WHOLESALE',
    reliability_score INTEGER DEFAULT 50,  -- 0-100, calculated
    avg_fulfillment_days FLOAT,
    last_fulfilled_at TIMESTAMPTZ,
    requires_login BOOLEAN NOT NULL DEFAULT false,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### `scraping_logs` — Granular Scrape Audit

```sql
CREATE TABLE scraping_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    status scrape_log_status NOT NULL,  -- SUCCESS, EMPTY, PARTIAL, FAILED_*
    items_scraped INTEGER NOT NULL DEFAULT 0,
    items_with_stock INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    raw_snapshot_path VARCHAR(500)  -- S3/path to raw HTML/JSON for debugging
);
```

#### `source_reliability_scores` — Historical Reliability

```sql
CREATE TABLE source_reliability_scores (
    id SERIAL PRIMARY KEY,
    source_id UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    month VARCHAR(7) NOT NULL,  -- "2026-06"
    fulfillment_rate FLOAT NOT NULL DEFAULT 0.0,  -- 0.0-1.0
    avg_stock_accuracy FLOAT NOT NULL DEFAULT 0.0,
    avg_delivery_days FLOAT,
    UNIQUE(source_id, month)
);
```

---

## Critical Bug Fixes Applied

### Bug 1: NameError — `stock` undefined in hash computation

**Before (BROKEN):**
```python
state_string = f"{raw_title}-{price}-{barcode}-{stock}".encode("utf-8")
# ❌ NameError: name 'stock' is not defined
```

**After (FIXED):**
```python
stock = extract_stock(raw_listing, source_type)  # Extract FIRST
current_hash = compute_state_hash(current_price, stock, availability.value)
# ✅ Stock is extracted before hash computation
```

**File:** `app/delta_engine.py:432-441`

---

### Bug 2: Availability state machine is dead code

**Before (BROKEN):**
```python
is_available = raw_listing.get("available", True) if current_price is not None else False
listing.availability = is_available  # ❌ Bypasses transition_availability()
```

**After (FIXED):**
```python
availability = determine_availability(stock, stock_confidence)
listing.transition_availability(availability, reason="scraped_update")
# ✅ Uses proper state machine with validation
```

**File:** `app/delta_engine.py:534`

---

### Bug 3: PriceHistory missing availability

**Before (BROKEN):**
```python
history = PriceHistory(listing_id=listing.id, price=current_price, stock=stock)
# ❌ Missing availability and source_name
```

**After (FIXED):**
```python
history = PriceHistory(
    listing_id=listing.id,
    price=current_price,
    stock=stock,
    availability=listing.availability,  # ✅ ADDED
    source_name=source.name,            # ✅ ADDED
)
```

**Files:** 
- `app/delta_engine.py:542-549` (update)
- `app/delta_engine.py:689-697` (create)

---

### Bug 4: is_available logic is backwards

**Before (BROKEN):**
```python
is_available = raw_listing.get("available", True) if current_price is not None else False
# ❌ Defaults to True, price alone does NOT mean available
```

**After (FIXED):**
```python
def determine_availability(stock: Optional[int], stock_confidence: StockConfidence) -> AvailabilityState:
    """
    B2B-first logic:
    - stock > 0 → AVAILABLE_IN_STOCK
    - stock == 0 → OUT_OF_STOCK
    - stock is None → UNKNOWN (do NOT default to available)
    """
    if stock is not None:
        if stock > 0:
            return AvailabilityState.AVAILABLE_IN_STOCK
        else:
            return AvailabilityState.OUT_OF_STOCK
    return AvailabilityState.UNKNOWN  # ✅ Never assume available
```

**File:** `app/delta_engine.py:193-211`

---

## Deduplication → Canonicalization Logic

### Current (Broken) Logic
Creates one Product and overwrites one Listing.

### Target Logic
**Product = canonical identity. Listings = per-source rows.**

### Normalization Pipeline

```python
def normalize_and_persist_task(raw_catalog, source_id):
    source = Source.query.get(source_id)
    
    for raw_item in raw_catalog:
        # 1. Extract all identifiers
        ean = clean_ean(raw_item.get("barcode"))
        title = clean_title(raw_item.get("title"))
        brand = extract_brand(title, raw_item.get("brand"))
        volume = extract_volume_ml(title)
        concentration = extract_concentration(title)
        variant_signature = f"{volume}ml-{concentration}"
        
        # 2. Find or create canonical Product
        product = find_or_create_product(
            ean=ean,
            brand=brand,
            name=extract_product_name(title),
            volume=volume,
            concentration=concentration,
        )
        
        # 3. Find or create Listing for this specific source + variant
        listing = ProductListing.query.filter_by(
            product_id=product.id,
            source_id=source.id,
            variant_signature=variant_signature
        ).first()
        
        if not listing:
            listing = ProductListing(...)
            db.session.add(listing)
        
        # 4. Update listing with scraped data
        previous_price = listing.current_price
        current_price = parse_price(raw_item.get("price"))
        stock = extract_stock(raw_item, source.engine_type)
        stock_confidence = determine_stock_confidence(raw_item, source)
        
        listing.current_price = current_price
        listing.current_stock = stock
        listing.stock_confidence = stock_confidence
        listing.moq = raw_item.get("moq")
        listing.bulk_pricing_tiers = raw_item.get("bulk_tiers", [])
        
        if stock_confidence in (StockConfidence.HIGH, StockConfidence.MEDIUM):
            listing.last_confirmed_stock_at = func.now()
        
        # 5. Transition availability state
        new_availability = determine_availability(stock, stock_confidence)
        listing.transition_availability(new_availability, reason="scraped_update")
        
        # 6. Record price history if changed
        if previous_price != current_price or listing.availability != previous_availability:
            history = PriceHistory(
                listing_id=listing.id,
                price=current_price,
                stock=stock,
                availability=listing.availability,
                source_name=source.name,
            )
            db.session.add(history)
    
    db.session.commit()
```

### Product Deduplication (Canonical Identity)

```python
def find_or_create_product(ean, brand, name, volume, concentration):
    # Priority 1: EAN-13/UPC exact match
    if ean:
        product = Product.query.filter_by(ean_13=ean).first()
        if product:
            return product
    
    # Priority 2: Normalized name + brand + volume + concentration
    normalized_name = normalize_perfume_name(name)
    product = Product.query.filter(
        func.lower(Product.brand) == brand.lower(),
        func.lower(Product.normalized_name) == normalized_name,
        Product.volume_ml == volume,
        Product.fragrance_type == concentration
    ).first()
    
    if product:
        return product
    
    # Priority 3: Create new canonical product
    product = Product.create_with_code(
        db,
        ean_13=ean,
        brand=brand,
        product_name=name,
        normalized_name=normalized_name,
        volume_ml=volume,
        fragrance_type=concentration,
    )
    db.session.flush()
    return product
```

---

## Stock Extraction Strategy

Per-source-type extractors:

```python
def extract_stock(raw_item: Dict[str, Any], source_type: str) -> Optional[int]:
    """Returns int stock count or None."""
    
    # Shopify B2B / Wholesale
    if source_type == "shopify":
        return raw_item.get("inventory_quantity")  # int or None
    
    # WooCommerce B2B
    if source_type in ("bs4_woocommerce", "woocommerce"):
        stock_text = raw_item.get("stock_text", "")
        if "in stock" in stock_text.lower():
            match = re.search(r'(\d+)\s+in stock', stock_text.lower())
            return int(match.group(1)) if match else 1
        elif "out of stock" in stock_text.lower():
            return 0
        return None
    
    # Generic / Unknown
    stock_val = raw_item.get("stock")
    if isinstance(stock_val, int):
        return stock_val
    if isinstance(stock_val, str) and stock_val.isdigit():
        return int(stock_val)
    
    return None


def determine_stock_confidence(raw_item, source):
    """How much do we trust this stock number?"""
    if source.business_type == "B2B_WHOLESALE" and raw_item.get("inventory_quantity") is not None:
        return StockConfidence.HIGH
    if raw_item.get("stock_text") and "in stock" in raw_item.get("stock_text", "").lower():
        return StockConfidence.MEDIUM
    if raw_item.get("available") is True:
        return StockConfidence.LOW
    return StockConfidence.UNKNOWN
```

---

## API Endpoints

### Core Endpoints

#### `GET /api/v1/products/{product_id}/comparison`

**The Core Feature:** Product comparison across all sources.

Response:
```json
{
  "product": {
    "id": "uuid",
    "product_code": "cyb00000123",
    "name": "Dior Sauvage",
    "brand": "Dior",
    "volume": 100,
    "concentration": "EDP",
    "ean": "3348901250153"
  },
  "listings": [
    {
      "source_id": "uuid",
      "source_name": "Distributor A",
      "source_reliability": 94,
      "price": 42.00,
      "currency": "USD",
      "stock": 15,
      "stock_confidence": "HIGH",
      "moq": 5,
      "bulk_tiers": [{"qty": 10, "price": 40.00}],
      "availability": "AVAILABLE_IN_STOCK",
      "last_seen": "2026-06-24T22:00:00Z",
      "last_confirmed_stock_at": "2026-06-24T22:00:00Z"
    }
  ],
  "best_price_in_stock": {
    "source_id": "uuid",
    "source_name": "Distributor A",
    "price": 42.00
  },
  "price_range": {
    "min": 40.50,
    "max": 44.00,
    "avg": 42.17
  }
}
```

#### `GET /api/v1/listings/{listing_id}/history`

Returns time-series price + availability for charting.

Response:
```json
{
  "listing_id": "uuid",
  "product_id": "uuid",
  "source_id": "uuid",
  "title": "Dior Sauvage 100ml EDP",
  "current_price": 42.00,
  "current_stock": 15,
  "current_availability": "AVAILABLE_IN_STOCK",
  "history": [
    {
      "recorded_at": "2026-06-20T10:00:00Z",
      "price": 45.00,
      "stock": 20,
      "availability": "AVAILABLE_IN_STOCK",
      "source_name": "Distributor A"
    }
  ]
}
```

#### `GET /api/v1/products?in_stock=true`

Product search with filters:
- `brand=Dior`
- `volume_ml=100`
- `fragrance_type=EDP`
- **`in_stock=true`** — Only products with at least ONE listing `AVAILABLE_IN_STOCK`

---

## Source Reliability Scoring

Background job that runs weekly:

```python
def calculate_source_reliability():
    for source in Source.query.filter_by(is_active=True):
        listings = ProductListing.query.filter_by(source_id=source.id).all()
        
        total = len(listings)
        with_stock = sum(1 for l in listings if l.stock_confidence in ("HIGH", "MEDIUM"))
        
        # Simple heuristic: what % of listings have verifiable stock data
        stock_accuracy = with_stock / total if total > 0 else 0
        
        # Base score 50 + stock_accuracy * 50 = 50-100 range
        source.reliability_score = int(50 + (stock_accuracy * 50))
        db.session.commit()
```

**Usage:**
```bash
python -m app.reliability_scorer
```

---

## Testing & Verification Criteria

### Bug Fixes
- [x] `delta_engine.py` no longer throws NameError on stock
- [x] `transition_availability()` is actually called
- [x] `PriceHistory` records include `availability` column
- [x] Out-of-stock items show `availability="OUT_OF_STOCK"`, not `AVAILABLE_IN_STOCK`

### Architecture
- [ ] Scraping the same product from 3 sources creates **1 Product + 3 ProductListings**
- [ ] Querying `/products/{id}/comparison` returns all 3 listings with distinct prices/stock
- [ ] Re-scraping same source updates existing listing, does NOT create duplicate

### Data Quality
- [x] Empty scrapes log `status="EMPTY"` and trigger admin alert
- [x] Scrapes with login failure log `status="FAILED_AUTH"`
- [x] `stock_confidence` is never NULL — always `HIGH/MEDIUM/LOW/UNKNOWN`
- [x] `last_confirmed_stock_at` updates only when confidence is `HIGH` or `MEDIUM`

### Schema
- [x] Migration runs successfully
- [x] `product_listings` has unique constraint on `(product_id, source_id, variant_signature)`
- [x] `moq`, `bulk_pricing_tiers`, `stock_confidence` columns exist and are queryable

---

## File Structure

```
perfume-intelligence-platform/
├── alembic/
│   └── versions/
│       ├── 001_initial_schema.py
│       └── 002_b2b_wholesale_schema_fixed.py
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI application + API endpoints
│   ├── models.py                  # SQLAlchemy ORM models
│   ├── delta_engine.py            # Core update logic (bug fixes applied)
│   ├── scraper_worker.py          # PostgreSQL queue worker
│   ├── reliability_scorer.py      # Background task for source scoring
│   ├── normalization.py           # Product name normalization
│   ├── database.py                # Database session management
│   ├── database_async.py          # Async database session
│   ├── exceptions.py              # Custom exception classes
│   ├── schemas.py                 # Pydantic schemas
│   └── scrapers/
│       ├── base.py
│       ├── factory.py
│       ├── shopify.py
│       ├── woocommerce.py
│       ├── jumpseller.py
│       └── playwright_b2b.py     # For login-required B2B portals
├── .env
├── requirements.txt
└── ARCHITECTURE.md               # This file
```

---

## Running the Platform

### 1. Apply Migrations
```bash
alembic upgrade head
```

### 2. Start API Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Start Scraper Worker
```bash
python -m app.scraper_worker
```

### 4. Run Reliability Scorer (Weekly)
```bash
python -m app.reliability_scorer
```

---

## Next Steps

1. **Test canonicalization**: Scrape same product from multiple sources, verify 1 Product + N Listings
2. **Test comparison API**: Query `/products/{id}/comparison`, verify all listings returned
3. **Monitor scraping_logs**: Verify `EMPTY` and `FAILED_*` states are logged
4. **Set up weekly cron**: Schedule `reliability_scorer.py` to run every Sunday
5. **Add integration tests**: Test full scrape → normalize → deduplicate → compare flow

---

**Architecture Status:** ✅ Complete
**Bug Fixes:** ✅ Applied
**Migration:** ✅ Applied (002_fixed)
**API Endpoints:** ✅ Implemented
**Background Tasks:** ✅ Implemented
