"""B2B wholesale schema — add new enums, columns, tables, and constraints

Revision ID: 002_fixed
Revises: 001
Create Date: 2026-06-25

Adds:
- New enum values: OUT_OF_STOCK, UNKNOWN to availability_state
- New enum values: EMPTY, FAILED_AUTH, FAILED_BLOCKED, FAILED_ERROR to scrape_status
- New enums: business_type, stock_confidence, scrape_log_status
- Source: business_type, reliability_score, avg_fulfillment_days, last_fulfilled_at, requires_login
- ProductListing: moq, bulk_pricing_tiers, stock_confidence, last_confirmed_stock_at, is_discontinued, variant_signature, currency
- ProductListing: unique constraint on (product_id, source_id, variant_signature)
- PriceHistory: availability, source_name columns
- ScrapeLog: items_with_stock column
- Product: suggested_retail_price, category, normalized_name columns
- New tables: product_categories, scraping_logs, source_reliability_scores
- Migrate AVAILABLE_NO_STOCK → OUT_OF_STOCK
- Change default availability from AVAILABLE_IN_STOCK to UNKNOWN
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers
revision: str = '002_fixed'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Get a direct connection for multi-step operations that need commits
    conn = op.get_bind()

    # ── 1. Add new values to existing enums (must be done outside transaction) ──
    # These use IF NOT EXISTS so they're safe to re-run
    conn.execute(sa.text("COMMIT"))  # End any open transaction
    conn.execute(sa.text("ALTER TYPE availability_state ADD VALUE IF NOT EXISTS 'OUT_OF_STOCK'"))
    conn.execute(sa.text("COMMIT"))  # Commit the enum value
    conn.execute(sa.text("ALTER TYPE availability_state ADD VALUE IF NOT EXISTS 'UNKNOWN'"))
    conn.execute(sa.text("COMMIT"))

    conn.execute(sa.text("ALTER TYPE scrape_status ADD VALUE IF NOT EXISTS 'EMPTY'"))
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text("ALTER TYPE scrape_status ADD VALUE IF NOT EXISTS 'FAILED_AUTH'"))
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text("ALTER TYPE scrape_status ADD VALUE IF NOT EXISTS 'FAILED_BLOCKED'"))
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text("ALTER TYPE scrape_status ADD VALUE IF NOT EXISTS 'FAILED_ERROR'"))
    conn.execute(sa.text("COMMIT"))

    # ── 2. Create new enum types ────────────────────────────────────────────
    business_type = sa.Enum(
        'B2B_WHOLESALE', 'B2C_RETAIL', 'MARKETPLACE',
        name='business_type',
    )
    business_type.create(op.get_bind(), checkfirst=True)

    stock_confidence = sa.Enum(
        'HIGH', 'MEDIUM', 'LOW', 'UNKNOWN',
        name='stock_confidence',
    )
    stock_confidence.create(op.get_bind(), checkfirst=True)

    scrape_log_status = sa.Enum(
        'SUCCESS', 'EMPTY', 'PARTIAL', 'FAILED_AUTH', 'FAILED_BLOCKED', 'FAILED_ERROR',
        name='scrape_log_status',
    )
    scrape_log_status.create(op.get_bind(), checkfirst=True)

    # ── 3. Migrate data: AVAILABLE_NO_STOCK → OUT_OF_STOCK ──────────────────
    # Now that enum values are committed, we can use them
    op.execute("""
        UPDATE product_listings
        SET availability = 'OUT_OF_STOCK'
        WHERE availability = 'AVAILABLE_NO_STOCK'
    """)
    op.execute("""
        UPDATE price_history
        SET availability = 'OUT_OF_STOCK'
        WHERE availability = 'AVAILABLE_NO_STOCK'
    """)

    # ── 4. Change default availability for new listings to UNKNOWN ──────────
    op.alter_column('product_listings', 'availability',
                    server_default='UNKNOWN')

    # ── 5. Add columns to sources ───────────────────────────────────────────
    op.add_column('sources', sa.Column(
        'business_type', sa.Enum(name='business_type'),
        nullable=True, server_default='B2B_WHOLESALE',
    ))
    op.add_column('sources', sa.Column(
        'reliability_score', sa.Integer, nullable=True, server_default='50',
    ))
    op.add_column('sources', sa.Column(
        'avg_fulfillment_days', sa.Float, nullable=True,
    ))
    op.add_column('sources', sa.Column(
        'last_fulfilled_at', sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column('sources', sa.Column(
        'requires_login', sa.Boolean, nullable=False, server_default='false',
    ))

    # ── 6. Add columns to products ──────────────────────────────────────────
    op.add_column('products', sa.Column(
        'suggested_retail_price', sa.Numeric(12, 2), nullable=True,
    ))
    op.add_column('products', sa.Column(
        'category', sa.String(100), nullable=True,
    ))
    op.add_column('products', sa.Column(
        'normalized_name', sa.String(255), nullable=True,
    ))
    op.create_index('ix_products_normalized_name', 'products', ['normalized_name'])

    # ── 7. Add columns to product_listings ──────────────────────────────────
    op.add_column('product_listings', sa.Column(
        'moq', sa.Integer, nullable=True,
    ))
    op.add_column('product_listings', sa.Column(
        'bulk_pricing_tiers', JSONB, nullable=True, server_default='[]',
    ))
    op.add_column('product_listings', sa.Column(
        'stock_confidence', sa.Enum(name='stock_confidence'),
        nullable=False, server_default='UNKNOWN',
    ))
    op.add_column('product_listings', sa.Column(
        'last_confirmed_stock_at', sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column('product_listings', sa.Column(
        'is_discontinued', sa.Boolean, nullable=False, server_default='false',
    ))
    op.add_column('product_listings', sa.Column(
        'variant_signature', sa.String(255), nullable=True,
    ))
    op.add_column('product_listings', sa.Column(
        'currency', sa.String(3), nullable=False, server_default='CLP',
    ))

    # ── 8. Add unique constraint on (product_id, source_id, variant_signature)
    # Check if constraint exists first, then add if it doesn't
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uix_listing_product_source_variant'
            ) THEN
                ALTER TABLE product_listings
                ADD CONSTRAINT uix_listing_product_source_variant
                UNIQUE (product_id, source_id, variant_signature);
            END IF;
        END $$;
    """)

    # ── 9. Add indexes for new columns ──────────────────────────────────────
    op.create_index('ix_listing_stock_confidence', 'product_listings', ['stock_confidence'], unique=False)
    op.create_index('ix_listing_variant_signature', 'product_listings', ['variant_signature'], unique=False)

    # ── 10. Add columns to price_history ────────────────────────────────────
    # Add availability column if not exists
    op.add_column('price_history', sa.Column(
        'availability', sa.Enum(name='availability_state'),
        nullable=True,  # Make nullable initially for existing rows
    ))
    op.add_column('price_history', sa.Column(
        'source_name', sa.String(255), nullable=True,
    ))

    # ── 11. Add items_with_stock to scrape_logs ────────────────────────────
    op.add_column('scrape_logs', sa.Column(
        'items_with_stock', sa.Integer, nullable=False, server_default='0',
    ))

    # ── 12. Create new table: product_categories ────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS product_categories (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) UNIQUE NOT NULL
        )
    """)

    # Seed default categories
    op.execute("""
        INSERT INTO product_categories (name) VALUES
            ('niche'), ('designer'), ('celebrity'), ('arabic'), ('exclusive'),
            ('mainstream'), ('indie')
        ON CONFLICT (name) DO NOTHING
    """)

    # ── 13. Create new table: scraping_logs ─────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS scraping_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            ended_at TIMESTAMP WITH TIME ZONE,
            status scrape_log_status NOT NULL,
            items_scraped INTEGER NOT NULL DEFAULT 0,
            items_with_stock INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            raw_snapshot_path VARCHAR(500)
        )
    """)
    op.create_index('ix_scraping_logs_source_time', 'scraping_logs', ['source_id', sa.text('started_at DESC')], unique=False)
    op.create_index('ix_scraping_logs_status', 'scraping_logs', ['status'], unique=False)

    # ── 14. Create new table: source_reliability_scores ─────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS source_reliability_scores (
            id SERIAL PRIMARY KEY,
            source_id UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            month VARCHAR(7) NOT NULL,
            fulfillment_rate FLOAT NOT NULL DEFAULT 0.0,
            avg_stock_accuracy FLOAT NOT NULL DEFAULT 0.0,
            avg_delivery_days FLOAT,
            UNIQUE(source_id, month)
        )
    """)
    op.create_index('ix_source_reliability_source', 'source_reliability_scores', ['source_id'], unique=False)

    # ── 15. Update price comparison function for new availability states ────
    op.execute("""
        CREATE OR REPLACE FUNCTION get_price_comparison(p_product_code VARCHAR(16))
        RETURNS TABLE (
            product_code VARCHAR(16), ean_13 VARCHAR(13), nombre TEXT,
            dif_precio_minimo DECIMAL(12,2), precio_minimo DECIMAL(12,2), nombre_mercado_minimo TEXT,
            dif_precio_min_stock DECIMAL(12,2), precio_min_stock DECIMAL(12,2), nombre_mercado_min_stock TEXT,
            precio_1 DECIMAL(12,2), stock_1 TEXT, precio_2 DECIMAL(12,2), stock_2 TEXT,
            precio_3 DECIMAL(12,2), stock_3 TEXT, precio_4 DECIMAL(12,2), stock_4 TEXT,
            precio_5 DECIMAL(12,2), stock_5 TEXT, precio_6 DECIMAL(12,2), stock_6 TEXT,
            precio_7 DECIMAL(12,2), stock_7 TEXT, precio_8 DECIMAL(12,2), stock_8 TEXT,
            precio_9 DECIMAL(12,2), stock_9 TEXT, precio_10 DECIMAL(12,2), stock_10 TEXT
        ) AS $$
        DECLARE
            v_product_id UUID;
            v_own_price DECIMAL(12,2) := NULL;
        BEGIN
            SELECT p.id INTO v_product_id FROM products p WHERE p.product_code = p_product_code;
            IF v_product_id IS NULL THEN RETURN; END IF;
            RETURN QUERY
            WITH source_map AS (
                SELECT id, name, ROW_NUMBER() OVER (ORDER BY name) as src_num FROM sources WHERE is_active = TRUE
            ),
            listings_grid AS (
                SELECT pl.product_id, sm.src_num, sm.name as source_name, pl.current_price,
                    CASE
                        WHEN pl.availability = 'AVAILABLE_IN_STOCK' THEN 'Sí'
                        WHEN pl.availability = 'OUT_OF_STOCK' THEN 'No'
                        WHEN pl.availability = 'UNKNOWN' THEN '?'
                        WHEN pl.availability = 'DELISTED' THEN 'No'
                        ELSE NULL
                    END as stock_flag
                FROM product_listings pl JOIN source_map sm ON pl.source_id = sm.id WHERE pl.product_id = v_product_id
            ),
            min_all AS (SELECT product_id, MIN(current_price) as min_price FROM listings_grid WHERE current_price IS NOT NULL GROUP BY product_id),
            min_stock AS (SELECT product_id, MIN(current_price) as min_price FROM listings_grid WHERE current_price IS NOT NULL AND stock_flag = 'Sí' GROUP BY product_id),
            min_all_src AS (SELECT product_id, string_agg(DISTINCT source_name, ', ' ORDER BY source_name) as names FROM listings_grid lg WHERE current_price = (SELECT min_price FROM min_all ma WHERE ma.product_id = lg.product_id) GROUP BY product_id),
            min_stock_src AS (SELECT product_id, string_agg(DISTINCT source_name, ', ' ORDER BY source_name) as names FROM listings_grid lg WHERE stock_flag = 'Sí' AND current_price = (SELECT min_price FROM min_stock ms WHERE ms.product_id = lg.product_id) GROUP BY product_id),
            pivoted AS (
                SELECT product_id,
                    MAX(CASE WHEN src_num = 1 THEN current_price END) as p1, MAX(CASE WHEN src_num = 1 THEN stock_flag END) as s1,
                    MAX(CASE WHEN src_num = 2 THEN current_price END) as p2, MAX(CASE WHEN src_num = 2 THEN stock_flag END) as s2,
                    MAX(CASE WHEN src_num = 3 THEN current_price END) as p3, MAX(CASE WHEN src_num = 3 THEN stock_flag END) as s3,
                    MAX(CASE WHEN src_num = 4 THEN current_price END) as p4, MAX(CASE WHEN src_num = 4 THEN stock_flag END) as s4,
                    MAX(CASE WHEN src_num = 5 THEN current_price END) as p5, MAX(CASE WHEN src_num = 5 THEN stock_flag END) as s5,
                    MAX(CASE WHEN src_num = 6 THEN current_price END) as p6, MAX(CASE WHEN src_num = 6 THEN stock_flag END) as s6,
                    MAX(CASE WHEN src_num = 7 THEN current_price END) as p7, MAX(CASE WHEN src_num = 7 THEN stock_flag END) as s7,
                    MAX(CASE WHEN src_num = 8 THEN current_price END) as p8, MAX(CASE WHEN src_num = 8 THEN stock_flag END) as s8,
                    MAX(CASE WHEN src_num = 9 THEN current_price END) as p9, MAX(CASE WHEN src_num = 9 THEN stock_flag END) as s9,
                    MAX(CASE WHEN src_num = 10 THEN current_price END) as p10, MAX(CASE WHEN src_num = 10 THEN stock_flag END) as s10
                FROM listings_grid GROUP BY product_id
            )
            SELECT p.product_code, p.ean_13,
                p.brand || ' ' || p.product_name || ' ' || COALESCE(p.variant || ' ', '') || COALESCE(p.volume_ml::TEXT || 'ml ', ''),
                CASE WHEN v_own_price IS NOT NULL AND ma.min_price > 0 THEN ROUND(((v_own_price - ma.min_price) / ma.min_price) * 100, 2) ELSE NULL END,
                ma.min_price, mas.names,
                CASE WHEN v_own_price IS NOT NULL AND ms.min_price > 0 THEN ROUND(((v_own_price - ms.min_price) / ms.min_price) * 100, 2) ELSE NULL END,
                ms.min_price, mss.names,
                pv.p1, pv.s1, pv.p2, pv.s2, pv.p3, pv.s3, pv.p4, pv.s4, pv.p5, pv.s5,
                pv.p6, pv.s6, pv.p7, pv.s7, pv.p8, pv.s8, pv.p9, pv.s9, pv.p10, pv.s10
            FROM products p
            LEFT JOIN min_all ma ON ma.product_id = p.id
            LEFT JOIN min_all_src mas ON mas.product_id = p.id
            LEFT JOIN min_stock ms ON ms.product_id = p.id
            LEFT JOIN min_stock_src mss ON mss.product_id = p.id
            LEFT JOIN pivoted pv ON pv.product_id = p.id
            WHERE p.id = v_product_id;
        EXCEPTION WHEN OTHERS THEN RETURN;
        END;
        $$ LANGUAGE plpgsql STABLE;
    """)


def downgrade() -> None:
    # Drop new tables
    op.drop_table('source_reliability_scores')
    op.drop_table('scraping_logs')
    op.drop_table('product_categories')

    # Drop columns from scrape_logs
    op.drop_column('scrape_logs', 'items_with_stock')

    # Drop columns from price_history
    op.drop_column('price_history', 'source_name')
    op.drop_column('price_history', 'availability')

    # Drop indexes and constraint from product_listings
    op.drop_index('ix_listing_variant_signature', table_name='product_listings')
    op.drop_index('ix_listing_stock_confidence', table_name='product_listings')
    op.execute("ALTER TABLE product_listings DROP CONSTRAINT IF EXISTS uix_listing_product_source_variant")

    # Drop columns from product_listings
    op.drop_column('product_listings', 'currency')
    op.drop_column('product_listings', 'variant_signature')
    op.drop_column('product_listings', 'is_discontinued')
    op.drop_column('product_listings', 'last_confirmed_stock_at')
    op.drop_column('product_listings', 'stock_confidence')
    op.drop_column('product_listings', 'bulk_pricing_tiers')
    op.drop_column('product_listings', 'moq')

    # Drop columns from products
    op.drop_index('ix_products_normalized_name', table_name='products')
    op.drop_column('products', 'normalized_name')
    op.drop_column('products', 'category')
    op.drop_column('products', 'suggested_retail_price')

    # Drop columns from sources
    op.drop_column('sources', 'requires_login')
    op.drop_column('sources', 'last_fulfilled_at')
    op.drop_column('sources', 'avg_fulfillment_days')
    op.drop_column('sources', 'reliability_score')
    op.drop_column('sources', 'business_type')

    # Revert availability default
    op.alter_column('product_listings', 'availability',
                    server_default='AVAILABLE_IN_STOCK')

    # Drop new enum types
    op.execute("DROP TYPE IF EXISTS scrape_log_status")
    op.execute("DROP TYPE IF EXISTS stock_confidence")
    op.execute("DROP TYPE IF EXISTS business_type")
