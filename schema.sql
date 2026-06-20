-- ============================================================
-- Perfume Intelligence Platform — PostgreSQL Schema
-- Database: perfume_intelligence_db
-- Market: Chile only. All prices in CLP.
-- ============================================================

-- ============================================================
-- ENUMS
-- ============================================================

CREATE TYPE availability_state AS ENUM (
    'AVAILABLE_IN_STOCK',
    'AVAILABLE_NO_STOCK',
    'DELISTED'
);
-- NOT_LISTED is implicit: absence of a product_listings row.

CREATE TYPE engine_type AS ENUM (
    'shopify',
    'bs4_woocommerce',
    'bs4_jumpseller',
    'playwright'
);

CREATE TYPE scrape_status AS ENUM (
    'STARTED',
    'SUCCESS',
    'PARTIAL',
    'FAIL'
);

CREATE TYPE normalization_method AS ENUM (
    'REGEX',
    'LLM_BEDROCK',
    'LLM_OLLAMA',
    'HYBRID',
    'MANUAL'
);

CREATE TYPE gender_type AS ENUM ('M', 'F', 'UNISEX');

CREATE TYPE fragrance_type AS ENUM ('EDP', 'EDT', 'PARFUM', 'COLOGNE', 'BODY_MIST');

-- ============================================================
-- TABLES
-- ============================================================

CREATE TABLE sources (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL UNIQUE,
    base_url        VARCHAR(512) NOT NULL,
    engine_type     engine_type NOT NULL,
    config          JSONB NOT NULL DEFAULT '{}',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    currency        CHAR(3) NOT NULL DEFAULT 'CLP',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_source_name_not_empty CHECK (LENGTH(TRIM(name)) > 0),
    CONSTRAINT chk_source_url_not_empty CHECK (LENGTH(TRIM(base_url)) > 0)
);

CREATE TABLE products (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_code        VARCHAR(16) NOT NULL UNIQUE,
    ean_13              VARCHAR(13),
    brand               VARCHAR(255) NOT NULL,
    product_name        VARCHAR(255) NOT NULL,
    variant             VARCHAR(255),
    fragrance_type      fragrance_type,
    volume_ml           INTEGER,
    gender              gender_type,
    search_vector       TSVECTOR,
    normalization_method normalization_method,
    confidence_score    DECIMAL(3,2) CHECK (confidence_score BETWEEN 0.0 AND 1.0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_product_identity UNIQUE NULLS NOT DISTINCT (
        brand, product_name, variant, volume_ml
    ),
    CONSTRAINT chk_product_code_format CHECK (
        product_code ~ '^[a-z]+[0-9]{8}$'
    ),
    CONSTRAINT chk_ean_13_format CHECK (
        ean_13 IS NULL OR (
            ean_13 ~ '^[0-9]{12,13}$' AND
            LENGTH(ean_13) IN (12, 13)
        )
    ),
    CONSTRAINT chk_brand_not_empty CHECK (LENGTH(TRIM(brand)) > 0),
    CONSTRAINT chk_product_name_not_empty CHECK (LENGTH(TRIM(product_name)) > 0),
    CONSTRAINT chk_volume_positive CHECK (volume_ml IS NULL OR volume_ml > 0)
);

CREATE TABLE product_listings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id          UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    source_id           UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    source_external_id  VARCHAR(255) NOT NULL,

    title               VARCHAR(500) NOT NULL,
    url                 VARCHAR(1024),
    image_url           VARCHAR(1024),

    current_hash        VARCHAR(64) NOT NULL,
    current_price       DECIMAL(12,2),
    current_stock       INTEGER,
    availability        availability_state NOT NULL DEFAULT 'AVAILABLE_IN_STOCK',

    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_scraped_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_listing_source_external_id UNIQUE (source_id, source_external_id),
    CONSTRAINT chk_listing_title_not_empty CHECK (LENGTH(TRIM(title)) > 0),
    CONSTRAINT chk_price_non_negative CHECK (current_price IS NULL OR current_price >= 0),
    CONSTRAINT chk_stock_non_negative CHECK (current_stock IS NULL OR current_stock >= 0)
);

CREATE TABLE price_history (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id  UUID NOT NULL REFERENCES product_listings(id) ON DELETE CASCADE,
    price       DECIMAL(12,2) NOT NULL,
    stock       INTEGER,
    availability availability_state NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_history_price_non_negative CHECK (price >= 0),
    CONSTRAINT chk_history_stock_non_negative CHECK (stock IS NULL OR stock >= 0)
);

CREATE TABLE price_tiers (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id  UUID NOT NULL REFERENCES product_listings(id) ON DELETE CASCADE,
    tier_name   VARCHAR(100) NOT NULL,
    price       DECIMAL(12,2) NOT NULL,
    currency    CHAR(3) NOT NULL DEFAULT 'CLP',
    valid_from  TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to    TIMESTAMPTZ,

    CONSTRAINT chk_tier_price_non_negative CHECK (price >= 0),
    CONSTRAINT chk_tier_name_not_empty CHECK (LENGTH(TRIM(tier_name)) > 0),
    CONSTRAINT uq_listing_tier_active UNIQUE (listing_id, tier_name, valid_from)
);

CREATE TABLE scrape_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    status              scrape_status NOT NULL,
    raw_storage_ref     VARCHAR(512),
    raw_data            JSONB,
    records_extracted   INTEGER NOT NULL DEFAULT 0,
    records_updated     INTEGER NOT NULL DEFAULT 0,
    records_skipped     INTEGER NOT NULL DEFAULT 0,
    records_failed      INTEGER NOT NULL DEFAULT 0,
    error_message       TEXT,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at            TIMESTAMPTZ,

    CONSTRAINT chk_records_non_negative CHECK (
        records_extracted >= 0 AND records_updated >= 0 AND
        records_skipped >= 0 AND records_failed >= 0
    )
);

CREATE TABLE scrape_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    priority        INTEGER NOT NULL DEFAULT 5,
    scheduled_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    error_message   TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 3,
    raw_data        JSONB,

    CONSTRAINT chk_queue_status CHECK (status IN ('PENDING', 'RUNNING', 'DONE', 'FAILED', 'CANCELLED')),
    CONSTRAINT chk_priority_range CHECK (priority BETWEEN 1 AND 10),
    CONSTRAINT chk_retry_non_negative CHECK (retry_count >= 0 AND max_retries >= 0)
);

-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX ix_products_search_vector ON products USING GIN(search_vector);
CREATE INDEX ix_products_brand_name ON products(brand, product_name);
CREATE INDEX ix_products_ean_13 ON products(ean_13) WHERE ean_13 IS NOT NULL;
CREATE INDEX ix_products_product_code ON products(product_code);
CREATE INDEX ix_products_normalization_method ON products(normalization_method) WHERE normalization_method IS NOT NULL;
CREATE INDEX ix_products_confidence_low ON products(confidence_score) WHERE confidence_score < 0.80;

CREATE INDEX ix_listing_product_source_price ON product_listings(product_id, source_id, current_price);
CREATE INDEX ix_listing_source_external ON product_listings(source_id, source_external_id);
CREATE INDEX ix_listing_last_seen_source ON product_listings(source_id, last_seen_at);
CREATE INDEX ix_listing_availability ON product_listings(availability);
CREATE INDEX ix_listing_product_availability ON product_listings(product_id, availability);

CREATE INDEX ix_price_history_listing_time ON price_history(listing_id, recorded_at DESC);
CREATE INDEX ix_price_history_recorded ON price_history(recorded_at DESC);

CREATE INDEX ix_scrape_logs_source_time ON scrape_logs(source_id, started_at DESC);
CREATE INDEX ix_scrape_logs_status ON scrape_logs(status);

CREATE INDEX ix_scrape_queue_scheduled ON scrape_queue(status, scheduled_at, priority DESC);
CREATE INDEX ix_scrape_queue_source ON scrape_queue(source_id, status);

-- ============================================================
-- TRIGGERS
-- ============================================================

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

CREATE TRIGGER trg_products_search_vector_update
    BEFORE INSERT OR UPDATE ON products
    FOR EACH ROW
    EXECUTE FUNCTION products_search_vector_update();

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

CREATE TRIGGER trg_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- PRICE COMPARISON FUNCTION (Coolebra-Equivalent)
-- ============================================================

CREATE OR REPLACE FUNCTION get_price_comparison(p_product_code VARCHAR(16))
RETURNS TABLE (
    product_code            VARCHAR(16),
    ean_13                  VARCHAR(13),
    nombre                  TEXT,
    dif_precio_minimo       DECIMAL(12,2),
    precio_minimo           DECIMAL(12,2),
    nombre_mercado_minimo   TEXT,
    dif_precio_min_stock    DECIMAL(12,2),
    precio_min_stock        DECIMAL(12,2),
    nombre_mercado_min_stock TEXT,
    precio_1  DECIMAL(12,2),  stock_1  TEXT,
    precio_2  DECIMAL(12,2),  stock_2  TEXT,
    precio_3  DECIMAL(12,2),  stock_3  TEXT,
    precio_4  DECIMAL(12,2),  stock_4  TEXT,
    precio_5  DECIMAL(12,2),  stock_5  TEXT,
    precio_6  DECIMAL(12,2),  stock_6  TEXT,
    precio_7  DECIMAL(12,2),  stock_7  TEXT,
    precio_8  DECIMAL(12,2),  stock_8  TEXT,
    precio_9  DECIMAL(12,2),  stock_9  TEXT,
    precio_10 DECIMAL(12,2),  stock_10 TEXT
) AS $$
DECLARE
    v_product_id UUID;
    v_own_price DECIMAL(12,2) := NULL;
BEGIN
    BEGIN
        SELECT p.id INTO v_product_id
        FROM products p WHERE p.product_code = p_product_code;
    EXCEPTION
        WHEN OTHERS THEN
            RAISE WARNING 'Error looking up product %: %', p_product_code, SQLERRM;
            RETURN;
    END;

    IF v_product_id IS NULL THEN
        RAISE WARNING 'Product not found: %', p_product_code;
        RETURN;
    END IF;

    RETURN QUERY
    WITH source_map AS (
        SELECT id, name, ROW_NUMBER() OVER (ORDER BY name) as src_num
        FROM sources WHERE is_active = TRUE
    ),
    listings_grid AS (
        SELECT
            pl.product_id,
            sm.src_num,
            sm.name as source_name,
            pl.current_price,
            CASE
                WHEN pl.availability = 'AVAILABLE_IN_STOCK' THEN 'Sí'
                WHEN pl.availability = 'AVAILABLE_NO_STOCK' THEN 'No'
                WHEN pl.availability = 'DELISTED' THEN 'No'
                ELSE NULL
            END as stock_flag
        FROM product_listings pl
        JOIN source_map sm ON pl.source_id = sm.id
        WHERE pl.product_id = v_product_id
    ),
    min_all AS (
        SELECT product_id, MIN(current_price) as min_price
        FROM listings_grid WHERE current_price IS NOT NULL GROUP BY product_id
    ),
    min_stock AS (
        SELECT product_id, MIN(current_price) as min_price
        FROM listings_grid
        WHERE current_price IS NOT NULL AND stock_flag = 'Sí'
        GROUP BY product_id
    ),
    min_all_src AS (
        SELECT product_id, string_agg(DISTINCT source_name, ', ' ORDER BY source_name) as names
        FROM listings_grid lg
        WHERE current_price = (SELECT min_price FROM min_all ma WHERE ma.product_id = lg.product_id)
        GROUP BY product_id
    ),
    min_stock_src AS (
        SELECT product_id, string_agg(DISTINCT source_name, ', ' ORDER BY source_name) as names
        FROM listings_grid lg
        WHERE stock_flag = 'Sí'
          AND current_price = (SELECT min_price FROM min_stock ms WHERE ms.product_id = lg.product_id)
        GROUP BY product_id
    ),
    pivoted AS (
        SELECT
            product_id,
            MAX(CASE WHEN src_num = 1 THEN current_price END) as p1,
            MAX(CASE WHEN src_num = 1 THEN stock_flag END) as s1,
            MAX(CASE WHEN src_num = 2 THEN current_price END) as p2,
            MAX(CASE WHEN src_num = 2 THEN stock_flag END) as s2,
            MAX(CASE WHEN src_num = 3 THEN current_price END) as p3,
            MAX(CASE WHEN src_num = 3 THEN stock_flag END) as s3,
            MAX(CASE WHEN src_num = 4 THEN current_price END) as p4,
            MAX(CASE WHEN src_num = 4 THEN stock_flag END) as s4,
            MAX(CASE WHEN src_num = 5 THEN current_price END) as p5,
            MAX(CASE WHEN src_num = 5 THEN stock_flag END) as s5,
            MAX(CASE WHEN src_num = 6 THEN current_price END) as p6,
            MAX(CASE WHEN src_num = 6 THEN stock_flag END) as s6,
            MAX(CASE WHEN src_num = 7 THEN current_price END) as p7,
            MAX(CASE WHEN src_num = 7 THEN stock_flag END) as s7,
            MAX(CASE WHEN src_num = 8 THEN current_price END) as p8,
            MAX(CASE WHEN src_num = 8 THEN stock_flag END) as s8,
            MAX(CASE WHEN src_num = 9 THEN current_price END) as p9,
            MAX(CASE WHEN src_num = 9 THEN stock_flag END) as s9,
            MAX(CASE WHEN src_num = 10 THEN current_price END) as p10,
            MAX(CASE WHEN src_num = 10 THEN stock_flag END) as s10
        FROM listings_grid
        GROUP BY product_id
    )
    SELECT
        p.product_code,
        p.ean_13,
        p.brand || ' ' || p.product_name || ' ' || COALESCE(p.variant || ' ', '') || COALESCE(p.volume_ml::TEXT || 'ml ', '') as nombre,
        CASE WHEN v_own_price IS NOT NULL AND ma.min_price > 0 THEN ROUND(((v_own_price - ma.min_price) / ma.min_price) * 100, 2) ELSE NULL END as dif_precio_minimo,
        ma.min_price as precio_minimo,
        mas.names as nombre_mercado_minimo,
        CASE WHEN v_own_price IS NOT NULL AND ms.min_price > 0 THEN ROUND(((v_own_price - ms.min_price) / ms.min_price) * 100, 2) ELSE NULL END as dif_precio_min_stock,
        ms.min_price as precio_min_stock,
        mss.names as nombre_mercado_min_stock,
        pv.p1, pv.s1, pv.p2, pv.s2, pv.p3, pv.s3, pv.p4, pv.s4, pv.p5, pv.s5,
        pv.p6, pv.s6, pv.p7, pv.s7, pv.p8, pv.s8, pv.p9, pv.s9, pv.p10, pv.s10
    FROM products p
    LEFT JOIN min_all ma ON ma.product_id = p.id
    LEFT JOIN min_all_src mas ON mas.product_id = p.id
    LEFT JOIN min_stock ms ON ms.product_id = p.id
    LEFT JOIN min_stock_src mss ON mss.product_id = p.id
    LEFT JOIN pivoted pv ON pv.product_id = p.id
    WHERE p.id = v_product_id;

EXCEPTION
    WHEN OTHERS THEN
        RAISE WARNING 'Price comparison failed for %: %', p_product_code, SQLERRM;
        RETURN;
END;
$$ LANGUAGE plpgsql STABLE;

-- Usage: SELECT * FROM get_price_comparison('a00000001');
