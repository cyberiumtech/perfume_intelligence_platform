"""
FastAPI application — Perfume Intelligence Platform v2

Endpoints:
  GET  /api/v1/health                          — Health check (tests DB)
  GET  /api/v1/sources                         — List all sources
  POST /api/v1/sources                         — Register a new source
  GET  /api/v1/products                        — Query products (filter by brand, type, gender, ml)
  GET  /api/v1/products/{product_code}         — Get product by code with listings
  POST /api/v1/trigger-scrape                  — Create scrape queue entry
  GET  /api/v1/scrape-logs                     — Recent scrape audit logs
  GET  /api/v1/price-comparison/{product_code} — Price comparison across sources

Fully modernized to use asynchronous routing and `asyncpg` for optimal concurrency.
"""
import logging
import uuid
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .database_async import get_db_async
from .exceptions import (
    PerfumePlatformError, ProductNotFoundError, SourceNotFoundError,
    ScraperError, NormalizationError, DatabaseError, BusinessLogicError,
    ConfigurationError,
)
from .models import (
    Source, Product, ProductListing, ScrapeLog, ScrapeQueue,
    AvailabilityState,
)
from .schemas import (
    SourceCreate, SourceSchema,
    ProductSchema, ProductListingSchema,
    ScrapeLogSchema, ScrapeQueueCreate, ScrapeQueueSchema,
    HealthResponse, TriggerScrapeResponse,
    PriceComparisonResult,
)

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# APP INSTANCE
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="Perfume Intelligence Platform v2",
    description="Chilean perfume market intelligence — multi-source aggregation & AI normalization",
    version="2.0.0",
)


# ══════════════════════════════════════════════════════════════════════════════
# EXCEPTION HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

@app.exception_handler(PerfumePlatformError)
async def platform_error_handler(request: Request, exc: PerfumePlatformError):
    """Handle all custom platform exceptions with structured JSON responses."""
    status_map = {
        ProductNotFoundError: 404,
        SourceNotFoundError: 404,
        BusinessLogicError: 422,
        ConfigurationError: 500,
        DatabaseError: 503,
        ScraperError: 502,
        NormalizationError: 500,
    }

    status_code = 500
    for exc_type, code in status_map.items():
        if isinstance(exc, exc_type):
            status_code = code
            break

    log.error(f"Platform error: {exc.to_dict()}")
    return JSONResponse(
        status_code=status_code,
        content=exc.to_dict(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/health", response_model=HealthResponse, tags=["System"])
async def health_check(db: AsyncSession = Depends(get_db_async)):
    """Verify API and database connectivity asynchronously."""
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected (async)"}
    except Exception as e:
        log.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail=f"Database error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SOURCES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/sources", response_model=List[SourceSchema], tags=["Sources"])
async def list_sources(db: AsyncSession = Depends(get_db_async)):
    """Return all configured scraping sources."""
    result = await db.execute(select(Source).order_by(Source.name))
    return result.scalars().all()


@app.post("/api/v1/sources", response_model=SourceSchema, status_code=201, tags=["Sources"])
async def create_source(payload: SourceCreate, db: AsyncSession = Depends(get_db_async)):
    """Register a new scraping source."""
    result = await db.execute(select(Source).filter(Source.name == payload.name))
    existing = result.scalars().first()
    
    if existing:
        raise HTTPException(status_code=409, detail=f"Source '{payload.name}' already exists")

    source = Source(**payload.model_dump())
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/products", response_model=List[ProductSchema], tags=["Products"])
async def list_products(
    brand: Optional[str] = Query(default=None),
    fragrance_type: Optional[str] = Query(default=None),
    gender: Optional[str] = Query(default=None),
    volume_ml: Optional[int] = Query(default=None, alias="ml"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, le=500),
    db: AsyncSession = Depends(get_db_async),
):
    """Query normalized canonical products with optional filters."""
    q = select(Product)

    if brand:
        q = q.filter(Product.brand.ilike(f"%{brand}%"))
    if fragrance_type:
        q = q.filter(Product.fragrance_type == fragrance_type.upper())
    if gender:
        q = q.filter(Product.gender == gender.upper())
    if volume_ml:
        q = q.filter(Product.volume_ml == volume_ml)

    q = q.order_by(Product.brand, Product.product_name).offset(skip).limit(limit)
    result = await db.execute(q)
    return result.scalars().all()


@app.get("/api/v1/products/{product_code}", tags=["Products"])
async def get_product(product_code: str, db: AsyncSession = Depends(get_db_async)):
    """Get a specific product by product_code with all its source listings."""
    # We MUST use selectinload here to eagerly load the relationship, otherwise
    # the async driver will crash with a MissingGreenletError when iterating listings.
    q = select(Product).options(selectinload(Product.listings)).filter(Product.product_code == product_code)
    result = await db.execute(q)
    product = result.scalars().first()
    
    if not product:
        raise HTTPException(status_code=404, detail=f"Product not found: {product_code}")

    return {
        "id": str(product.id),
        "product_code": product.product_code,
        "ean_13": product.ean_13,
        "brand": product.brand,
        "product_name": product.product_name,
        "variant": product.variant,
        "fragrance_type": product.fragrance_type.value if product.fragrance_type else None,
        "volume_ml": product.volume_ml,
        "gender": product.gender.value if product.gender else None,
        "normalization_method": product.normalization_method.value if product.normalization_method else None,
        "confidence_score": float(product.confidence_score) if product.confidence_score else None,
        "created_at": product.created_at.isoformat(),
        "updated_at": product.updated_at.isoformat(),
        "listings": [
            {
                "id": str(l.id),
                "source_id": str(l.source_id),
                "source_external_id": l.source_external_id,
                "title": l.title,
                "url": l.url,
                "image_url": l.image_url,
                "current_price": float(l.current_price) if l.current_price else None,
                "current_stock": l.current_stock,
                "availability": l.availability.value if hasattr(l.availability, 'value') else l.availability,
                "last_seen_at": l.last_seen_at.isoformat() if l.last_seen_at else None,
                "last_scraped_at": l.last_scraped_at.isoformat() if l.last_scraped_at else None,
            }
            for l in product.listings
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
# SCRAPE TRIGGER
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/v1/trigger-scrape", response_model=TriggerScrapeResponse, tags=["Scraping"])
async def trigger_scrape(
    payload: ScrapeQueueCreate,
    db: AsyncSession = Depends(get_db_async),
):
    """
    Trigger a scrape by creating a ScrapeQueue entry.
    The scraper worker polls this queue.
    """
    # Validate UUID
    try:
        source_uuid = uuid.UUID(str(payload.source_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source_id UUID format")

    # Check source exists
    result = await db.execute(select(Source).filter(Source.id == source_uuid))
    source = result.scalars().first()
    
    if not source:
        raise HTTPException(status_code=404, detail=f"Source not found: {payload.source_id}")

    if not source.is_active:
        raise HTTPException(status_code=422, detail=f"Source '{source.name}' is inactive")

    # Create queue entry
    queue_entry = ScrapeQueue(
        source_id=source_uuid,
        priority=payload.priority,
    )
    db.add(queue_entry)
    await db.commit()
    await db.refresh(queue_entry)

    return {
        "message": "Scrape queued successfully",
        "queue_id": str(queue_entry.id),
        "source_id": str(payload.source_id),
    }


@app.post("/api/v1/trigger-scrape-all", tags=["Scraping"])
async def trigger_scrape_all(
    db: AsyncSession = Depends(get_db_async),
):
    """
    Trigger a scrape for ALL active sources.
    This is used by the frontend 'Update All' button.
    """
    result = await db.execute(select(Source).filter(Source.is_active == True))
    sources = result.scalars().all()
    
    if not sources:
        raise HTTPException(status_code=404, detail="No active sources found to scrape.")

    queue_entries = []
    for source in sources:
        entry = ScrapeQueue(source_id=source.id, priority=5)
        db.add(entry)
        queue_entries.append(entry)
    
    await db.commit()

    return {
        "message": f"Scrape queued successfully for {len(sources)} active sources.",
        "queued_sources": len(sources)
    }


# ══════════════════════════════════════════════════════════════════════════════
# SCRAPE LOGS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/scrape-logs", response_model=List[ScrapeLogSchema], tags=["Scraping"])
async def list_scrape_logs(
    source_id: Optional[str] = Query(default=None),
    limit: int = Query(default=20, le=100),
    db: AsyncSession = Depends(get_db_async),
):
    """Return recent scrape audit logs, optionally filtered by source."""
    q = select(ScrapeLog)
    if source_id:
        q = q.filter(ScrapeLog.source_id == source_id)
        
    q = q.order_by(ScrapeLog.started_at.desc()).limit(limit)
    result = await db.execute(q)
    return result.scalars().all()


# ══════════════════════════════════════════════════════════════════════════════
# PRICE COMPARISON
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/price-comparison/{product_code}", tags=["Products"])
async def get_price_comparison(product_code: str, db: AsyncSession = Depends(get_db_async)):
    """
    Get price comparison across all sources for a product.
    Calls the get_price_comparison SQL function.
    """
    result = await db.execute(
        text("SELECT * FROM get_price_comparison(:code)"),
        {"code": product_code},
    )
    row = result.first()

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No price comparison data for product: {product_code}",
        )

    # Map result columns to response
    columns = [
        "product_code", "ean_13", "nombre",
        "dif_precio_minimo", "precio_minimo", "nombre_mercado_minimo",
        "dif_precio_min_stock", "precio_min_stock", "nombre_mercado_min_stock",
        "precio_1", "stock_1", "precio_2", "stock_2",
        "precio_3", "stock_3", "precio_4", "stock_4",
        "precio_5", "stock_5", "precio_6", "stock_6",
        "precio_7", "stock_7", "precio_8", "stock_8",
        "precio_9", "stock_9", "precio_10", "stock_10",
    ]

    response = {}
    for i, col in enumerate(columns):
        val = row[i] if i < len(row) else None
        response[col] = float(val) if val is not None and col.startswith(("precio", "dif_")) else val

    return response