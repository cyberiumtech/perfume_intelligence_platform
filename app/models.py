"""
SQLAlchemy 2.0 ORM models for the Perfume Intelligence Platform.

Architecture: One Product, Many Listings
- products table: canonical identity ONLY (brand, name, volume, concentration)
- product_listings table: one row PER source PER product variant
- Comparison happens across listings, not by collapsing them

Maps directly to the PostgreSQL schema in schema.sql.
All enum types use native PostgreSQL enums.
No Base.metadata.create_all() — use Alembic migrations only.
"""
import enum
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Column, String, Integer, Numeric, DateTime, ForeignKey, Text, Boolean,
    Float, Index, UniqueConstraint, CheckConstraint, Enum, event,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, TSVECTOR
from sqlalchemy.orm import declarative_base, relationship, Session

from .exceptions import InvalidStateTransitionError, ProductCodeError
from .product_code_generator import get_next_code_from_db, generate_emergency_code

log = logging.getLogger(__name__)

Base = declarative_base()


# ══════════════════════════════════════════════════════════════════════════════
# ENUMS (mirror PostgreSQL CREATE TYPE)
# ══════════════════════════════════════════════════════════════════════════════

class AvailabilityState(str, enum.Enum):
    AVAILABLE_IN_STOCK = "AVAILABLE_IN_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    UNKNOWN = "UNKNOWN"
    DELISTED = "DELISTED"


class EngineType(str, enum.Enum):
    SHOPIFY = "shopify"
    BS4_WOOCOMMERCE = "bs4_woocommerce"
    BS4_JUMPSELLER = "bs4_jumpseller"
    PLAYWRIGHT = "playwright"


class ScrapeStatus(str, enum.Enum):
    STARTED = "STARTED"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    EMPTY = "EMPTY"
    FAIL = "FAIL"
    FAILED_AUTH = "FAILED_AUTH"
    FAILED_BLOCKED = "FAILED_BLOCKED"
    FAILED_ERROR = "FAILED_ERROR"


class NormalizationMethod(str, enum.Enum):
    REGEX = "REGEX"
    LLM_BEDROCK = "LLM_BEDROCK"
    LLM_OLLAMA = "LLM_OLLAMA"
    LLM_GEMINI = "LLM_GEMINI"
    LLM_OPENAI_BEDROCK = "LLM_OPENAI_BEDROCK"
    HYBRID = "HYBRID"
    MANUAL = "MANUAL"


class GenderType(str, enum.Enum):
    M = "M"
    F = "F"
    UNISEX = "UNISEX"


class FragranceType(str, enum.Enum):
    EDP = "EDP"
    EDT = "EDT"
    PARFUM = "PARFUM"
    COLOGNE = "COLOGNE"
    BODY_MIST = "BODY_MIST"


class BusinessType(str, enum.Enum):
    B2B_WHOLESALE = "B2B_WHOLESALE"
    B2C_RETAIL = "B2C_RETAIL"
    MARKETPLACE = "MARKETPLACE"


class StockConfidence(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class ScrapeLogStatus(str, enum.Enum):
    SUCCESS = "SUCCESS"
    EMPTY = "EMPTY"
    PARTIAL = "PARTIAL"
    FAILED_AUTH = "FAILED_AUTH"
    FAILED_BLOCKED = "FAILED_BLOCKED"
    FAILED_ERROR = "FAILED_ERROR"


# Valid availability state transitions
_AVAILABILITY_TRANSITIONS = {
    AvailabilityState.AVAILABLE_IN_STOCK: {
        AvailabilityState.OUT_OF_STOCK,
        AvailabilityState.UNKNOWN,
        AvailabilityState.DELISTED,
    },
    AvailabilityState.OUT_OF_STOCK: {
        AvailabilityState.AVAILABLE_IN_STOCK,
        AvailabilityState.UNKNOWN,
        AvailabilityState.DELISTED,
    },
    AvailabilityState.UNKNOWN: {
        AvailabilityState.AVAILABLE_IN_STOCK,
        AvailabilityState.OUT_OF_STOCK,
        AvailabilityState.DELISTED,
    },
    AvailabilityState.DELISTED: set(),  # Terminal — no transitions allowed
}


# ══════════════════════════════════════════════════════════════════════════════
# MODELS
# ══════════════════════════════════════════════════════════════════════════════

class Source(Base):
    """
    A scraping source — one row per Chilean distributor website.
    engine_type drives which scraper strategy is used.
    config stores site-specific options (selectors, pagination, etc.).
    """
    __tablename__ = "sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), unique=True, nullable=False)
    base_url = Column(String(512), nullable=False)
    engine_type = Column(
        Enum(EngineType, name="engine_type", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    config = Column(JSONB, nullable=False, default=dict)
    is_active = Column(Boolean, nullable=False, default=True)
    currency = Column(String(3), nullable=False, default="CLP")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # B2B wholesale fields
    business_type = Column(
        Enum(BusinessType, name="business_type", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=True,
        default=BusinessType.B2B_WHOLESALE,
    )
    reliability_score = Column(Integer, nullable=True, default=50)  # 0-100, calculated
    avg_fulfillment_days = Column(Float, nullable=True)
    last_fulfilled_at = Column(DateTime(timezone=True), nullable=True)
    requires_login = Column(Boolean, nullable=False, default=False)

    # Relationships
    listings = relationship("ProductListing", back_populates="source", cascade="all, delete-orphan")
    scrape_logs = relationship("ScrapeLog", back_populates="source", cascade="all, delete-orphan")
    scrape_queue_entries = relationship("ScrapeQueue", back_populates="source", cascade="all, delete-orphan")
    scraping_logs = relationship("ScrapingLog", back_populates="source", cascade="all, delete-orphan")
    reliability_scores = relationship("SourceReliabilityScore", back_populates="source", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Source {self.name} ({self.engine_type})>"


class ProductCategory(Base):
    """
    Product categories for filtering (niche, designer, celebrity, arabic, etc.).
    """
    __tablename__ = "product_categories"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)

    def __repr__(self):
        return f"<ProductCategory {self.name}>"


class Product(Base):
    """
    Canonical master record for a unique perfume product.
    One Product = one distinct (brand, product_name, variant, volume_ml) combination.
    Multiple sources can link to the same Product via ProductListing.
    """
    __tablename__ = "products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_code = Column(String(16), unique=True, nullable=False)
    ean_13 = Column(String(13), nullable=True)
    brand = Column(String(255), nullable=False)
    product_name = Column(String(255), nullable=False)
    variant = Column(String(255), nullable=True)
    fragrance_type = Column(
        Enum(FragranceType, name="fragrance_type", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=True,
    )
    volume_ml = Column(Integer, nullable=True)
    gender = Column(
        Enum(GenderType, name="gender_type", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=True,
    )
    search_vector = Column(TSVECTOR, nullable=True)
    normalization_method = Column(
        Enum(NormalizationMethod, name="normalization_method", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=True,
    )
    confidence_score = Column(Numeric(3, 2), nullable=True)
    suggested_retail_price = Column(Numeric(12, 2), nullable=True)
    category = Column(String(100), nullable=True)  # References product_categories.name
    normalized_name = Column(String(255), nullable=True)  # Lowercase normalized for dedup
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    listings = relationship("ProductListing", back_populates="product", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint(
            "brand", "product_name", "variant", "volume_ml",
            name="uq_product_identity",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_products_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_products_brand_name", "brand", "product_name"),
        Index("ix_products_ean_13", "ean_13", postgresql_where="ean_13 IS NOT NULL"),
        Index("ix_products_normalized_name", "normalized_name"),
    )

    @classmethod
    def create_with_code(cls, db: Session, **kwargs) -> "Product":
        """
        Create a new Product with an auto-generated product_code.

        Uses SELECT FOR UPDATE SKIP LOCKED for thread safety.
        Falls back to emergency code generation on failure.

        Args:
            db: SQLAlchemy session (must be in a transaction)
            **kwargs: Product fields (brand, product_name, etc.)

        Returns:
            New Product instance (added to session but not committed)
        """
        try:
            code = get_next_code_from_db(db)
        except Exception as e:
            log.warning(f"Sequential code generation failed, using emergency code: {e}")
            code = generate_emergency_code()

        product = cls(product_code=code, **kwargs)
        db.add(product)
        return product

    def __repr__(self):
        return f"<Product {self.product_code}: {self.brand} – {self.product_name} {self.volume_ml}ml>"


class ProductListing(Base):
    """
    A product as listed on a specific source at a specific price.
    Links a canonical Product to a Source.
    Acts as the current-state snapshot — updated on every scrape.

    One row PER source PER product variant.
    Comparison happens across listings, not by collapsing them.
    """
    __tablename__ = "product_listings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    source_id = Column(UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    source_external_id = Column(String(255), nullable=False)

    title = Column(String(500), nullable=False)
    url = Column(String(1024), nullable=True)
    image_url = Column(String(1024), nullable=True)

    current_hash = Column(String(64), nullable=False)
    current_price = Column(Numeric(12, 2), nullable=True)
    current_stock = Column(Integer, nullable=True)
    availability = Column(
        Enum(AvailabilityState, name="availability_state", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=AvailabilityState.UNKNOWN,
    )

    # B2B wholesale fields
    moq = Column(Integer, nullable=True)  # Minimum Order Quantity
    bulk_pricing_tiers = Column(JSONB, nullable=True, default=list)  # e.g., [{"qty": 10, "price": 40.0}, ...]
    stock_confidence = Column(
        Enum(StockConfidence, name="stock_confidence", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=StockConfidence.UNKNOWN,
    )
    last_confirmed_stock_at = Column(DateTime(timezone=True), nullable=True)
    is_discontinued = Column(Boolean, nullable=False, default=False)
    variant_signature = Column(String(255), nullable=True)  # e.g., "100ml-EDP-Boxed"
    currency = Column(String(3), nullable=False, default="CLP")

    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_scraped_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    product = relationship("Product", back_populates="listings")
    source = relationship("Source", back_populates="listings")
    price_history = relationship("PriceHistory", back_populates="listing", cascade="all, delete-orphan")
    price_tiers = relationship("PriceTier", back_populates="listing", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("source_id", "source_external_id", name="uq_listing_source_external_id"),
        UniqueConstraint("product_id", "source_id", "variant_signature", name="uix_listing_product_source_variant"),
        Index("ix_listing_product_source_price", "product_id", "source_id", "current_price"),
        Index("ix_listing_source_external", "source_id", "source_external_id"),
        Index("ix_listing_last_seen_source", "source_id", "last_seen_at"),
        Index("ix_listing_availability", "availability"),
        Index("ix_listing_product_availability", "product_id", "availability"),
        Index("ix_listing_stock_confidence", "stock_confidence"),
        Index("ix_listing_variant_signature", "variant_signature"),
        CheckConstraint("current_stock IS NULL OR current_stock >= 0", name="chk_stock_non_negative"),
        CheckConstraint("current_price IS NULL OR current_price >= 0", name="chk_price_non_negative"),
    )

    def transition_availability(self, new_state: AvailabilityState, reason: str = None) -> None:
        """
        Validate and apply an availability state transition.

        Valid transitions:
            AVAILABLE_IN_STOCK → OUT_OF_STOCK | UNKNOWN | DELISTED
            OUT_OF_STOCK → AVAILABLE_IN_STOCK | UNKNOWN | DELISTED
            UNKNOWN → AVAILABLE_IN_STOCK | OUT_OF_STOCK | DELISTED
            DELISTED → (none — terminal state)

        Args:
            new_state: The target availability state
            reason: Optional reason for the transition (e.g., "scraped_update")

        Raises:
            InvalidStateTransitionError: If the transition is not allowed
        """
        current = self.availability
        if isinstance(current, str):
            current = AvailabilityState(current)
        if isinstance(new_state, str):
            new_state = AvailabilityState(new_state)

        if current == new_state:
            return  # No-op: same state

        allowed = _AVAILABILITY_TRANSITIONS.get(current, set())
        if new_state not in allowed:
            raise InvalidStateTransitionError(
                f"Cannot transition from {current.value} to {new_state.value}",
                from_state=current.value,
                to_state=new_state.value,
            )

        log.info(
            f"Availability transition: {current.value} → {new_state.value}"
            f"{f' (reason: {reason})' if reason else ''}"
            f" for listing {self.id}"
        )
        self.availability = new_state

    def __repr__(self):
        return f"<Listing [{self.source_id}] {self.title[:50]} @ {self.current_price}>"


class PriceHistory(Base):
    """
    Append-only time-series of price and stock for a ProductListing.
    One record inserted every time a price/stock/availability change is detected.
    """
    __tablename__ = "price_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id = Column(UUID(as_uuid=True), ForeignKey("product_listings.id", ondelete="CASCADE"), nullable=False)
    price = Column(Numeric(12, 2), nullable=False)
    stock = Column(Integer, nullable=True)
    availability = Column(
        Enum(AvailabilityState, name="availability_state", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    source_name = Column(String(255), nullable=True)  # Denormalized for fast querying
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    listing = relationship("ProductListing", back_populates="price_history")

    __table_args__ = (
        Index("ix_price_history_listing_time", "listing_id", recorded_at.desc()),
        Index("ix_price_history_recorded", recorded_at.desc()),
        CheckConstraint("price >= 0", name="chk_history_price_non_negative"),
        CheckConstraint("stock IS NULL OR stock >= 0", name="chk_history_stock_non_negative"),
    )

    def __repr__(self):
        return f"<PriceHistory listing={self.listing_id} price={self.price}>"


class PriceTier(Base):
    """
    Wholesale/retail price tiers for a ProductListing.
    Supports multiple concurrent tiers with validity windows.
    """
    __tablename__ = "price_tiers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id = Column(UUID(as_uuid=True), ForeignKey("product_listings.id", ondelete="CASCADE"), nullable=False)
    tier_name = Column(String(100), nullable=False)
    price = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="CLP")
    valid_from = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    valid_to = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    listing = relationship("ProductListing", back_populates="price_tiers")

    __table_args__ = (
        UniqueConstraint("listing_id", "tier_name", "valid_from", name="uq_listing_tier_active"),
    )

    def __repr__(self):
        return f"<PriceTier {self.tier_name} @ {self.price} {self.currency}>"


class ScrapeLog(Base):
    """
    Audit log for each scrape run. One record per source per run.
    """
    __tablename__ = "scrape_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id = Column(UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    status = Column(
        Enum(ScrapeStatus, name="scrape_status", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    raw_storage_ref = Column(String(512), nullable=True)
    raw_data = Column(JSONB, nullable=True)
    records_extracted = Column(Integer, nullable=False, default=0)
    records_updated = Column(Integer, nullable=False, default=0)
    records_skipped = Column(Integer, nullable=False, default=0)
    records_failed = Column(Integer, nullable=False, default=0)
    items_with_stock = Column(Integer, nullable=False, default=0)  # How many had stock data
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    ended_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    source = relationship("Source", back_populates="scrape_logs")

    __table_args__ = (
        Index("ix_scrape_logs_source_time", "source_id", started_at.desc()),
        Index("ix_scrape_logs_status", "status"),
    )

    def __repr__(self):
        return f"<ScrapeLog source={self.source_id} status={self.status}>"


class ScrapingLog(Base):
    """
    Enhanced scraping audit log with granular status tracking.
    Replaces silent failures with explicit non-success states.
    """
    __tablename__ = "scraping_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id = Column(UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    ended_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(
        Enum(ScrapeLogStatus, name="scrape_log_status", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    items_scraped = Column(Integer, nullable=False, default=0)
    items_with_stock = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    raw_snapshot_path = Column(String(500), nullable=True)  # S3/path to raw HTML/JSON for debugging

    # Relationships
    source = relationship("Source", back_populates="scraping_logs")

    __table_args__ = (
        Index("ix_scraping_logs_source_time", "source_id", started_at.desc()),
        Index("ix_scraping_logs_status", "status"),
    )

    def __repr__(self):
        return f"<ScrapingLog source={self.source_id} status={self.status}>"


class ScrapeQueue(Base):
    """
    PostgreSQL-based job queue (pg-boss pattern).
    Replaces Celery + Redis for Phase 1.
    """
    __tablename__ = "scrape_queue"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id = Column(UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(20), nullable=False, default="PENDING")
    priority = Column(Integer, nullable=False, default=5)
    scheduled_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    raw_data = Column(JSONB, nullable=True)

    # Relationships
    source = relationship("Source", back_populates="scrape_queue_entries")

    __table_args__ = (
        Index("ix_scrape_queue_scheduled", "status", "scheduled_at", priority.desc()),
        Index("ix_scrape_queue_source", "source_id", "status"),
    )

    def __repr__(self):
        return f"<ScrapeQueue source={self.source_id} status={self.status}>"


class SourceReliabilityScore(Base):
    """
    Historical reliability tracking per source, calculated monthly.
    """
    __tablename__ = "source_reliability_scores"

    id = Column(Integer, primary_key=True)
    source_id = Column(UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True)
    month = Column(String(7), nullable=False)  # "2026-06"
    fulfillment_rate = Column(Float, nullable=False, default=0.0)  # 0.0-1.0
    avg_stock_accuracy = Column(Float, nullable=False, default=0.0)  # How often stock claim matched reality
    avg_delivery_days = Column(Float, nullable=True)

    # Relationships
    source = relationship("Source", back_populates="reliability_scores")

    __table_args__ = (
        UniqueConstraint("source_id", "month", name="uq_reliability_source_month"),
    )

    def __repr__(self):
        return f"<SourceReliabilityScore source={self.source_id} month={self.month}>"


# ══════════════════════════════════════════════════════════════════════════════
# EVENT LISTENERS
# ══════════════════════════════════════════════════════════════════════════════

@event.listens_for(Product, "before_insert")
def validate_product_code_before_insert(mapper, connection, target):
    """Validate that product_code is set and matches the required format before insert."""
    if not target.product_code:
        raise ProductCodeError("product_code must be set before inserting a Product")

    import re
    if not re.match(r'^[a-z]+[0-9]{8}$', target.product_code):
        raise ProductCodeError(
            f"Invalid product_code format: '{target.product_code}'. "
            f"Expected pattern: [a-z]+[0-9]{{8}}"
        )