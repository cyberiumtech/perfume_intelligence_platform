"""
Pydantic v2 schemas for the Perfume Intelligence Platform API.

Handles serialization/deserialization for all endpoints.
All schemas use ConfigDict(from_attributes=True) for ORM compatibility.
"""
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, Dict, Any, Literal, List
from uuid import UUID
from datetime import datetime
from decimal import Decimal


# ══════════════════════════════════════════════════════════════════════════════
# AI / NORMALIZATION SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class AIProductExtraction(BaseModel):
    """Validation schema for LLM normalization output."""
    brand: str = Field(description="The perfume brand/house")
    product_name: str = Field(description="The specific fragrance name only")
    variant: Optional[str] = Field(default=None, description="Variant or edition")
    fragrance_type: Optional[str] = Field(default=None, description="EDP, EDT, PARFUM, COLOGNE, or BODY_MIST")
    ml: Optional[int] = Field(default=None, description="Volume in milliliters")


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class SourceBase(BaseModel):
    name: str
    base_url: str
    engine_type: str
    config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    currency: str = "CLP"


class SourceCreate(SourceBase):
    pass


class SourceSchema(SourceBase):
    id: UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCT SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class ProductBase(BaseModel):
    product_code: str
    ean_13: Optional[str] = None
    brand: str
    product_name: str
    variant: Optional[str] = None
    fragrance_type: Optional[str] = None
    volume_ml: Optional[int] = None
    gender: Optional[Literal["M", "F", "UNISEX"]] = None
    normalization_method: Optional[str] = None
    confidence_score: Optional[Decimal] = None


class ProductCreate(BaseModel):
    brand: str
    product_name: str
    variant: Optional[str] = None
    fragrance_type: Optional[str] = None
    volume_ml: Optional[int] = None
    gender: Optional[Literal["M", "F", "UNISEX"]] = None
    ean_13: Optional[str] = None


class ProductSchema(ProductBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCT LISTING SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class ProductListingBase(BaseModel):
    source_external_id: str
    title: str
    url: Optional[str] = None
    image_url: Optional[str] = None
    current_hash: str
    current_price: Optional[Decimal] = None
    current_stock: Optional[int] = None
    availability: str = "AVAILABLE_IN_STOCK"


class ProductListingCreate(ProductListingBase):
    product_id: UUID
    source_id: UUID


class ProductListingSchema(ProductListingBase):
    id: UUID
    product_id: UUID
    source_id: UUID
    last_seen_at: datetime
    last_scraped_at: datetime
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ══════════════════════════════════════════════════════════════════════════════
# PRICE HISTORY SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class PriceHistorySchema(BaseModel):
    id: UUID
    listing_id: UUID
    price: Decimal
    stock: Optional[int] = None
    availability: str
    recorded_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ══════════════════════════════════════════════════════════════════════════════
# PRICE TIER SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class PriceTierSchema(BaseModel):
    id: UUID
    listing_id: UUID
    tier_name: str
    price: Decimal
    currency: str
    valid_from: datetime
    valid_to: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ══════════════════════════════════════════════════════════════════════════════
# SCRAPE LOG SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class ScrapeLogSchema(BaseModel):
    id: UUID
    source_id: UUID
    status: str
    raw_storage_ref: Optional[str] = None
    records_extracted: int = 0
    records_updated: int = 0
    records_skipped: int = 0
    records_failed: int = 0
    error_message: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ══════════════════════════════════════════════════════════════════════════════
# SCRAPE QUEUE SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class ScrapeQueueCreate(BaseModel):
    source_id: UUID
    priority: int = Field(default=5, ge=1, le=10)


class ScrapeQueueSchema(BaseModel):
    id: UUID
    source_id: UUID
    status: str
    priority: int
    scheduled_at: datetime
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    model_config = ConfigDict(from_attributes=True)


# ══════════════════════════════════════════════════════════════════════════════
# COMPOSITE / API RESPONSE SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class ProductWithListings(ProductSchema):
    """Product with all its source listings."""
    listings: List[ProductListingSchema] = []


class PriceComparisonResult(BaseModel):
    """Result from the get_price_comparison SQL function."""
    product_code: Optional[str] = None
    ean_13: Optional[str] = None
    nombre: Optional[str] = None
    dif_precio_minimo: Optional[Decimal] = None
    precio_minimo: Optional[Decimal] = None
    nombre_mercado_minimo: Optional[str] = None
    dif_precio_min_stock: Optional[Decimal] = None
    precio_min_stock: Optional[Decimal] = None
    nombre_mercado_min_stock: Optional[str] = None
    precio_1: Optional[Decimal] = None
    stock_1: Optional[str] = None
    precio_2: Optional[Decimal] = None
    stock_2: Optional[str] = None
    precio_3: Optional[Decimal] = None
    stock_3: Optional[str] = None
    precio_4: Optional[Decimal] = None
    stock_4: Optional[str] = None
    precio_5: Optional[Decimal] = None
    stock_5: Optional[str] = None
    precio_6: Optional[Decimal] = None
    stock_6: Optional[str] = None
    precio_7: Optional[Decimal] = None
    stock_7: Optional[str] = None
    precio_8: Optional[Decimal] = None
    stock_8: Optional[str] = None
    precio_9: Optional[Decimal] = None
    stock_9: Optional[str] = None
    precio_10: Optional[Decimal] = None
    stock_10: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    database: str


class TriggerScrapeResponse(BaseModel):
    message: str
    queue_id: str
    source_id: str
