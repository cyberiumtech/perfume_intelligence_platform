"""Initial schema — Perfume Intelligence Platform v2

Revision ID: 001
Revises: None
Create Date: 2026-06-19

Creates all tables, enums, indexes, triggers, and the price comparison function
for the perfume_intelligence_db database.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, TSVECTOR

# revision identifiers
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enums ──────────────────────────────────────────────────────────────
    availability_state = sa.Enum(
        'AVAILABLE_IN_STOCK', 'AVAILABLE_NO_STOCK', 'DELISTED',
        name='availability_state',
    )
    engine_type = sa.Enum(
        'shopify', 'bs4_woocommerce', 'bs4_jumpseller', 'playwright',
        name='engine_type',
    )
    scrape_status = sa.Enum(
        'STARTED', 'SUCCESS', 'PARTIAL', 'FAIL',
        name='scrape_status',
    )
    normalization_method = sa.Enum(
        'REGEX', 'LLM_BEDROCK', 'LLM_OLLAMA', 'HYBRID', 'MANUAL',
        name='normalization_method',
    )
    gender_type = sa.Enum('M', 'F', 'UNISEX', name='gender_type')
    fragrance_type = sa.Enum(
        'EDP', 'EDT', 'PARFUM', 'COLOGNE', 'BODY_MIST',
        name='fragrance_type',
    )

    availability_state.create(op.get_bind(), checkfirst=True)
    engine_type.create(op.get_bind(), checkfirst=True)
    scrape_status.create(op.get_bind(), checkfirst=True)
    normalization_method.create(op.get_bind(), checkfirst=True)
    gender_type.create(op.get_bind(), checkfirst=True)
    fragrance_type.create(op.get_bind(), checkfirst=True)

    # ── Sources ────────────────────────────────────────────────────────────
    op.create_table(
        'sources',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(255), nullable=False, unique=True),
        sa.Column('base_url', sa.String(512), nullable=False),
        sa.Column('engine_type', engine_type, nullable=False),
        sa.Column('config', JSONB, nullable=False, server_default='{}'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('currency', sa.String(3), nullable=False, server_default='CLP'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.CheckConstraint("LENGTH(TRIM(name)) > 0", name='chk_source_name_not_empty'),
        sa.CheckConstraint("LENGTH(TRIM(base_url)) > 0", name='chk_source_url_not_empty'),
    )

    # ── Products ───────────────────────────────────────────────────────────
    op.create_table(
        'products',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('product_code', sa.String(16), nullable=False, unique=True),
        sa.Column('ean_13', sa.String(13), nullable=True),
        sa.Column('brand', sa.String(255), nullable=False),
        sa.Column('product_name', sa.String(255), nullable=False),
        sa.Column('variant', sa.String(255), nullable=True),
        sa.Column('fragrance_type', fragrance_type, nullable=True),
        sa.Column('volume_ml', sa.Integer, nullable=True),
        sa.Column('gender', gender_type, nullable=True),
        sa.Column('search_vector', TSVECTOR, nullable=True),
        sa.Column('normalization_method', normalization_method, nullable=True),
        sa.Column('confidence_score', sa.Numeric(3, 2), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.UniqueConstraint('brand', 'product_name', 'variant', 'volume_ml',
                           name='uq_product_identity', postgresql_nulls_not_distinct=True),
        sa.CheckConstraint("product_code ~ '^[a-z]+[0-9]{8}$'", name='chk_product_code_format'),
        sa.CheckConstraint(
            "ean_13 IS NULL OR (ean_13 ~ '^[0-9]{12,13}$' AND LENGTH(ean_13) IN (12, 13))",
            name='chk_ean_13_format',
        ),
        sa.CheckConstraint("LENGTH(TRIM(brand)) > 0", name='chk_brand_not_empty'),
        sa.CheckConstraint("LENGTH(TRIM(product_name)) > 0", name='chk_product_name_not_empty'),
        sa.CheckConstraint("volume_ml IS NULL OR volume_ml > 0", name='chk_volume_positive'),
        sa.CheckConstraint("confidence_score IS NULL OR (confidence_score BETWEEN 0.0 AND 1.0)",
                          name='chk_confidence_range'),
    )

    # ── Product Listings ───────────────────────────────────────────────────
    op.create_table(
        'product_listings',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('product_id', UUID(as_uuid=True), sa.ForeignKey('products.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_id', UUID(as_uuid=True), sa.ForeignKey('sources.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_external_id', sa.String(255), nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('url', sa.String(1024), nullable=True),
        sa.Column('image_url', sa.String(1024), nullable=True),
        sa.Column('current_hash', sa.String(64), nullable=False),
        sa.Column('current_price', sa.Numeric(12, 2), nullable=True),
        sa.Column('current_stock', sa.Integer, nullable=True),
        sa.Column('availability', availability_state, nullable=False, server_default='AVAILABLE_IN_STOCK'),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('last_scraped_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.UniqueConstraint('source_id', 'source_external_id', name='uq_listing_source_external_id'),
        sa.CheckConstraint("LENGTH(TRIM(title)) > 0", name='chk_listing_title_not_empty'),
        sa.CheckConstraint("current_price IS NULL OR current_price >= 0", name='chk_price_non_negative'),
        sa.CheckConstraint("current_stock IS NULL OR current_stock >= 0", name='chk_stock_non_negative'),
    )

    # ── Price History ──────────────────────────────────────────────────────
    op.create_table(
        'price_history',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('listing_id', UUID(as_uuid=True), sa.ForeignKey('product_listings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('price', sa.Numeric(12, 2), nullable=False),
        sa.Column('stock', sa.Integer, nullable=True),
        sa.Column('availability', availability_state, nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.CheckConstraint("price >= 0", name='chk_history_price_non_negative'),
        sa.CheckConstraint("stock IS NULL OR stock >= 0", name='chk_history_stock_non_negative'),
    )

    # ── Price Tiers ────────────────────────────────────────────────────────
    op.create_table(
        'price_tiers',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('listing_id', UUID(as_uuid=True), sa.ForeignKey('product_listings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tier_name', sa.String(100), nullable=False),
        sa.Column('price', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False, server_default='CLP'),
        sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('listing_id', 'tier_name', 'valid_from', name='uq_listing_tier_active'),
        sa.CheckConstraint("price >= 0", name='chk_tier_price_non_negative'),
        sa.CheckConstraint("LENGTH(TRIM(tier_name)) > 0", name='chk_tier_name_not_empty'),
    )

    # ── Scrape Logs ────────────────────────────────────────────────────────
    op.create_table(
        'scrape_logs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('source_id', UUID(as_uuid=True), sa.ForeignKey('sources.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', scrape_status, nullable=False),
        sa.Column('raw_storage_ref', sa.String(512), nullable=True),
        sa.Column('raw_data', JSONB, nullable=True),
        sa.Column('records_extracted', sa.Integer, nullable=False, server_default='0'),
        sa.Column('records_updated', sa.Integer, nullable=False, server_default='0'),
        sa.Column('records_skipped', sa.Integer, nullable=False, server_default='0'),
        sa.Column('records_failed', sa.Integer, nullable=False, server_default='0'),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "records_extracted >= 0 AND records_updated >= 0 AND records_skipped >= 0 AND records_failed >= 0",
            name='chk_records_non_negative',
        ),
    )

    # ── Scrape Queue ───────────────────────────────────────────────────────
    op.create_table(
        'scrape_queue',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('source_id', UUID(as_uuid=True), sa.ForeignKey('sources.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='PENDING'),
        sa.Column('priority', sa.Integer, nullable=False, server_default='5'),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('retry_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('max_retries', sa.Integer, nullable=False, server_default='3'),
        sa.Column('raw_data', JSONB, nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'DONE', 'FAILED', 'CANCELLED')",
            name='chk_queue_status',
        ),
        sa.CheckConstraint("priority BETWEEN 1 AND 10", name='chk_priority_range'),
        sa.CheckConstraint("retry_count >= 0 AND max_retries >= 0", name='chk_retry_non_negative'),
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    op.create_index('ix_products_ean_13', 'products', ['ean_13'], postgresql_where=sa.text('ean_13 IS NOT NULL'))
    op.create_index('ix_products_product_code', 'products', ['product_code'])
    op.create_index('ix_products_normalization_method', 'products', ['normalization_method'],
                    postgresql_where=sa.text('normalization_method IS NOT NULL'))
    op.create_index('ix_products_confidence_low', 'products', ['confidence_score'],
                    postgresql_where=sa.text('confidence_score < 0.80'))

    op.create_index('ix_price_history_listing_time', 'price_history', ['listing_id', sa.text('recorded_at DESC')])
    op.create_index('ix_price_history_recorded', 'price_history', [sa.text('recorded_at DESC')])

    op.create_index('ix_scrape_logs_source_time', 'scrape_logs', ['source_id', sa.text('started_at DESC')])
    op.create_index('ix_scrape_logs_status', 'scrape_logs', ['status'])

    op.create_index('ix_scrape_queue_scheduled', 'scrape_queue', ['status', 'scheduled_at', sa.text('priority DESC')])
    op.create_index('ix_scrape_queue_source', 'scrape_queue', ['source_id', 'status'])

    # ── Triggers (raw SQL) ─────────────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION products_search_vector_update()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('spanish', COALESCE(NEW.brand, '')), 'A') ||
                setweight(to_tsvector('spanish', COALESCE(NEW.product_name, '')), 'A') ||
                setweight(to_tsvector('spanish', COALESCE(NEW.variant, '')), 'B') ||
                setweight(to_tsvector('spanish', COALESCE(NEW.fragrance_type::text, '')), 'C') ||
                setweight(to_tsvector('spanish', COALESCE(NEW.volume_ml::text, '')), 'C');
            RETURN NEW;
        EXCEPTION
            WHEN OTHERS THEN
                RAISE WARNING 'search_vector update failed for product %: %', NEW.id, SQLERRM;
                NEW.search_vector := NULL;
                RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trg_products_search_vector_update
            BEFORE INSERT OR UPDATE ON products
            FOR EACH ROW
            EXECUTE FUNCTION products_search_vector_update();
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        EXCEPTION
            WHEN OTHERS THEN
                RAISE WARNING 'updated_at trigger failed for %: %', TG_TABLE_NAME, SQLERRM;
                RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trg_products_updated_at
            BEFORE UPDATE ON products
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
    """)

    # ── Price Comparison Function ──────────────────────────────────────────
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
                    CASE WHEN pl.availability = 'AVAILABLE_IN_STOCK' THEN 'Sí' ELSE 'No' END as stock_flag
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
    # Drop in reverse order
    op.execute("DROP FUNCTION IF EXISTS get_price_comparison(VARCHAR)")
    op.execute("DROP TRIGGER IF EXISTS trg_products_updated_at ON products")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column()")
    op.execute("DROP TRIGGER IF EXISTS trg_products_search_vector_update ON products")
    op.execute("DROP FUNCTION IF EXISTS products_search_vector_update()")

    op.drop_table('scrape_queue')
    op.drop_table('scrape_logs')
    op.drop_table('price_tiers')
    op.drop_table('price_history')
    op.drop_table('product_listings')
    op.drop_table('products')
    op.drop_table('sources')

    op.execute("DROP TYPE IF EXISTS availability_state")
    op.execute("DROP TYPE IF EXISTS engine_type")
    op.execute("DROP TYPE IF EXISTS scrape_status")
    op.execute("DROP TYPE IF EXISTS normalization_method")
    op.execute("DROP TYPE IF EXISTS gender_type")
    op.execute("DROP TYPE IF EXISTS fragrance_type")
