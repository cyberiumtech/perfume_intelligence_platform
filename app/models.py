import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Numeric, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB, TSVECTOR
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import Index, UniqueConstraint

Base = declarative_base()


class Product(Base):
    """
    Canonical master record for a unique perfume product.
    One Product = one distinct (brand, product_name, variant, ml) combination.
    Multiple sources can link to the same Product via ProductListing.
    """
    __tablename__ = "products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ean_13 = Column(String(14), unique=True, index=True, nullable=True)  # EAN-13 or UPC barcode
    brand = Column(String, nullable=False, index=True)
    product_name = Column(String, nullable=False)
    variant = Column(String, nullable=True)                  # e.g., "Intense", "Sport"
    fragrance_type = Column(String, nullable=True)           # EDP / EDT / PARFUM / COLOGNE / BODY_MIST
    ml = Column(Integer, nullable=True, index=True)          # Volume in milliliters
    gender = Column(String(6), nullable=True)                # M / F / UNISEX
    search_vector = Column(TSVECTOR, nullable=True)          # GIN-indexed for full-text search
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    listings = relationship("ProductListing", back_populates="product", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("brand", "product_name", "variant", "ml", name="uq_product_identity", postgresql_nulls_not_distinct=True),
        Index("ix_products_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_products_brand_name", "brand", "product_name"),
    )

    def __repr__(self):
        return f"<Product {self.brand} – {self.product_name} {self.ml}ml>"


class Source(Base):
    """
    A scraping source — one row per Chilean distributor website.
    engine_type drives which scraper strategy is used.
    config stores site-specific options (login creds, selectors, etc.).
    """
    __tablename__ = "sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, unique=True, nullable=False)
    base_url = Column(String, nullable=False)
    engine_type = Column(String, nullable=False)  # shopify | bs4_woocommerce | bs4_jumpseller | playwright
    config = Column(JSONB, default=dict, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    listings = relationship("ProductListing", back_populates="source")
    scrape_logs = relationship("ScrapeLog", back_populates="source")

    def __repr__(self):
        return f"<Source {self.name} ({self.engine_type})>"


class ProductListing(Base):
    """
    A product as listed on a specific source at a specific price.
    Links a canonical Product to a Source.
    Acts as the current-state snapshot — updated on every scrape.
    """
    __tablename__ = "product_listings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    source_id = Column(UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    source_external_id = Column(String, nullable=False)     # SKU or stable ID at source
    title = Column(String, nullable=False)                   # Raw title as scraped
    url = Column(String, nullable=True)                      # Product page URL at source
    image_url = Column(String, nullable=True)                # Primary product image URL
    current_hash = Column(String(64), index=True, nullable=True)  # SHA-256 of (price+title+stock)
    current_price = Column(Numeric(12, 2), nullable=True)   # Price in CLP
    current_stock = Column(Integer, nullable=True)           # Stock count
    is_available = Column(Boolean, default=True, nullable=False)
    last_seen_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    product = relationship("Product", back_populates="listings")
    source = relationship("Source", back_populates="listings")
    price_history = relationship("PriceHistory", back_populates="listing", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("source_id", "source_external_id", name="uq_listing_source_external_id"),
        Index("ix_listing_source_id", "source_id"),
    )

    def __repr__(self):
        return f"<Listing [{self.source_id}] {self.title} @ {self.current_price}>"


class PriceHistory(Base):
    """
    Append-only time-series of price and stock for a ProductListing.
    One record inserted every time a price/stock change is detected.
    """
    __tablename__ = "price_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id = Column(UUID(as_uuid=True), ForeignKey("product_listings.id", ondelete="CASCADE"), nullable=False)
    price = Column(Numeric(12, 2), nullable=False)
    stock = Column(Integer, nullable=True)
    recorded_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True
    )

    listing = relationship("ProductListing", back_populates="price_history")

    def __repr__(self):
        return f"<PriceHistory listing={self.listing_id} price={self.price}>"


class ScrapeLog(Base):
    """
    Audit log for each scrape run. One record per source per run.
    """
    __tablename__ = "scrape_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id = Column(UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    status = Column(String, nullable=False)                 # STARTED / SUCCESS / FAIL / PARTIAL
    s3_raw_uri = Column(String, nullable=True)              # S3 path of raw JSON backup
    records_extracted = Column(Integer, default=0)
    records_updated = Column(Integer, default=0)
    records_skipped = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    ended_at = Column(DateTime(timezone=True), nullable=True)

    source = relationship("Source", back_populates="scrape_logs")

    def __repr__(self):
        return f"<ScrapeLog source={self.source_id} status={self.status}>"