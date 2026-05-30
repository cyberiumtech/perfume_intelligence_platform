from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, Dict, Any, Literal
from uuid import UUID
from datetime import datetime
from decimal import Decimal


# ── Bedrock Output Validation Schema ────────────────────────────────────────

class AIProductExtraction(BaseModel):
    brand: str = Field(description="The perfume brand/house (e.g., 'Carolina Herrera', 'Dior')")
    product_name: str = Field(description="The specific fragrance name only, no brand/size/type")
    variant: Optional[str] = Field(default=None, description="Specific variant or edition, if any")
    fragrance_type: Optional[str] = Field(
        default=None,
        description="Must be one of: EDP, EDT, PARFUM, COLOGNE, BODY_MIST"
    )
    ml: Optional[int] = Field(default=None, description="Volume in milliliters (integer)")


# ── Source Schemas ────────────────────────────────────────────────────────────

class SourceBase(BaseModel):
    name: str
    base_url: str
    engine_type: str
    config: Dict[str, Any] = Field(default_factory=dict)

class SourceCreate(SourceBase):
    pass

class SourceSchema(SourceBase):
    id: UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── Product Schemas ───────────────────────────────────────────────────────────

class ProductBase(BaseModel):
    ean_13: Optional[str] = None
    brand: str
    product_name: str
    variant: Optional[str] = None
    fragrance_type: Optional[str] = None
    ml: Optional[int] = None
    gender: Optional[Literal["M", "F", "UNISEX"]] = None

class ProductCreate(ProductBase):
    pass

class ProductSchema(ProductBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── ProductListing Schemas ────────────────────────────────────────────────────

class ProductListingBase(BaseModel):
    source_external_id: str
    title: str
    url: str
    image_url: Optional[str] = None
    current_hash: str
    current_price: Decimal
    current_stock: Optional[int] = None

class ProductListingCreate(ProductListingBase):
    product_id: UUID
    source_id: UUID

class ProductListingSchema(ProductListingBase):
    id: UUID
    product_id: UUID
    source_id: UUID
    last_seen_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── Price History ─────────────────────────────────────────────────────────────

class PriceHistorySchema(BaseModel):
    id: UUID
    listing_id: UUID
    price: Decimal
    stock: Optional[int] = None
    recorded_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── API Response Schemas ─────────────────────────────────────────────────────

class ProductWithListings(ProductSchema):
    listings: list[ProductListingSchema] = []

class ScrapeLogSchema(BaseModel):
    id: UUID
    source_id: UUID
    status: str
    s3_raw_uri: Optional[str] = None
    records_extracted: Optional[int] = 0
    error_message: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)
