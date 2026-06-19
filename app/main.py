# app/main.py
"""
FastAPI application — Perfume Intelligence Platform API

Endpoints:
  GET  /api/v1/health              — Health check
  GET  /api/v1/sources             — List all configured sources
  POST /api/v1/sources             — Register a new source
  POST /api/v1/trigger-scrape      — Manually trigger a scrape
  GET  /api/v1/products            — Query normalized products
  GET  /api/v1/products/{id}       — Get a specific product with listings
  GET  /api/v1/scrape-logs         — Recent scrape audit logs
"""
from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
import uuid

from .database import engine, get_db
from .models import Base, Source, Product, ProductListing, ScrapeLog
from .schemas import (
    SourceCreate, SourceSchema,
    ProductSchema, ProductListingSchema,
    ScrapeLogSchema,
)
from .worker import orchestrate_scrape_task

# Ensure all tables exist on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Perfume Intelligence Platform",
    description="Chilean perfume market intelligence — multi-source aggregation & AI normalization",
    version="1.0.0",
)


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/api/v1/health", tags=["System"])
def health_check(db: Session = Depends(get_db)):
    """Verify API and database connectivity."""
    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")


# ── Sources ──────────────────────────────────────────────────────────────────

@app.get("/api/v1/sources", response_model=List[SourceSchema], tags=["Sources"])
def list_sources(db: Session = Depends(get_db)):
    """Return all configured scraping sources."""
    return db.query(Source).order_by(Source.name).all()


@app.post("/api/v1/sources", response_model=SourceSchema, status_code=201, tags=["Sources"])
def create_source(payload: SourceCreate, db: Session = Depends(get_db)):
    """Register a new scraping source."""
    existing = db.query(Source).filter(Source.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Source '{payload.name}' already exists")
    source = Source(**payload.model_dump())
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@app.post("/api/v1/trigger-scrape", tags=["Scraping"])
def trigger_scrape(source_id: str, limit: Optional[int] = Query(default=None)):
    """
    Manually trigger a scrape for a specific source.
    Dispatches an async Celery task and returns immediately.
    """
    try:
        uuid.UUID(source_id)  # Validate UUID format
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source_id UUID format")

    task = orchestrate_scrape_task.delay(source_id, limit)
    return {
        "message": "Scrape task dispatched successfully",
        "task_id": task.id,
        "source_id": source_id,
    }


# ── Products ─────────────────────────────────────────────────────────────────

@app.get("/api/v1/products", response_model=List[ProductSchema], tags=["Products"])
def list_products(
    brand: Optional[str] = Query(default=None),
    fragrance_type: Optional[str] = Query(default=None),
    gender: Optional[str] = Query(default=None),
    ml: Optional[int] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, le=500),
    db: Session = Depends(get_db),
):
    """Query normalized canonical products with optional filters."""
    q = db.query(Product)
    if brand:
        escaped_brand = brand.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        q = q.filter(Product.brand.ilike(f"%{escaped_brand}%", escape="\\"))
    if fragrance_type:
        q = q.filter(Product.fragrance_type == fragrance_type.upper())
    if gender:
        q = q.filter(Product.gender == gender.upper())
    if ml:
        q = q.filter(Product.ml == ml)
    return q.order_by(Product.brand, Product.product_name).offset(skip).limit(limit).all()


@app.get("/api/v1/products/{product_id}", tags=["Products"])
def get_product(product_id: str, db: Session = Depends(get_db)):
    """Get a specific product with all its source listings and prices."""
    try:
        pid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid product_id UUID")
    product = db.query(Product).filter(Product.id == pid).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return {
        "id": str(product.id),
        "brand": product.brand,
        "product_name": product.product_name,
        "variant": product.variant,
        "fragrance_type": product.fragrance_type,
        "ml": product.ml,
        "gender": product.gender,
        "ean_13": product.ean_13,
        "listings": [
            {
                "source_id": str(l.source_id),
                "title": l.title,
                "url": l.url,
                "image_url": l.image_url,
                "current_price": float(l.current_price) if l.current_price else None,
                "current_stock": l.current_stock,
                "is_available": l.is_available,
                "last_seen_at": l.last_seen_at.isoformat() if l.last_seen_at else None,
            }
            for l in product.listings
        ]
    }


# ── Scrape Logs ───────────────────────────────────────────────────────────────

@app.get("/api/v1/scrape-logs", response_model=List[ScrapeLogSchema], tags=["Scraping"])
def list_scrape_logs(
    source_id: Optional[str] = Query(default=None),
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    """Return recent scrape audit logs, optionally filtered by source."""
    q = db.query(ScrapeLog)
    if source_id:
        q = q.filter(ScrapeLog.source_id == source_id)
    return q.order_by(ScrapeLog.started_at.desc()).limit(limit).all()