# Perfume Intelligence Platform

Welcome to the **Perfume Intelligence Platform**! This platform is designed to be the ultimate intelligence hub for the Chilean perfume market. It aggregates, normalizes, and tracks pricing and availability of perfume products across multiple wholesale and retail distributors.

---

## 📖 Introduction & Objective

**Objective:**
To provide a reliable, automated, and highly normalized intelligence database of perfume products in the Chilean market by extracting data from various distributor sources, tracking price changes over time, and normalizing diverse product titles into clean, canonical records.

**Why it matters:**
Distributors use widely varying names, formats, and structures for the same perfumes. The core value of this platform is not just scraping, but its **AI-driven normalization pipeline** which ensures that a "Dior Sauvage 100ml" listed differently across 5 websites maps to **one** single canonical product in our database.

---

## 🏗️ System Architecture

The system is built as an asynchronous, distributed pipeline to handle long-running scraping tasks, AI inference, and structured data storage without blocking the main API.

### Core Components
1. **FastAPI Backend (`app/main.py`)**: The central REST API. Exposes endpoints to trigger scrapes, list sources, and query normalized products.
2. **PostgreSQL Database (`app/models.py`)**: The source of truth. Uses SQLAlchemy ORM to manage Canonical Products, Listings, Price History, Sources, and Logs.
3. **Celery Worker (`app/worker.py`)**: The background processing engine. Orchestrates scrapes, dispatches normalization tasks, and saves to the database. Backed by Redis as a message broker.
4. **Scraper Factory (`scrapers/`)**: A modular architecture supporting multiple extraction strategies (Shopify APIs, BeautifulSoup HTML parsing, B2B Playwright instances).
5. **AI Pipeline (`app/ai_pipeline.py`)**: The data normalization engine powered by AWS Bedrock (Anthropic Claude 3 Haiku). It turns messy titles into structured data.
6. **Amazon S3**: Used as a raw data lake to dump extracted catalogs for backup and debugging before they go through normalization.

---

## 🔍 Low-Level Designs

### 1. The Scraper Factory (`scrapers/`)
The system defines a unified `BaseScraper` contract. All scrapers must return a list of `RawListing` dataclass objects.
The `ScraperFactory` instantiates the correct strategy based on the source's `engine_type`:
* **`shopify`**: Directly hits the `/products.json` endpoint for ultra-fast, structured extraction.
* **`bs4_woocommerce`**: Parses WordPress/WooCommerce HTML pages using BeautifulSoup.
* **`bs4_jumpseller`**: Parses Jumpseller SaaS HTML storefronts.
* **`playwright`**: A headless browser strategy capable of handling B2B logins and executing JavaScript before extracting the DOM.

### 2. The Asynchronous Task Pipeline (`worker.py`)
Scraping can be slow, so the pipeline is heavily decoupled using Celery:
1. **`orchestrate_scrape_task`**: Kicks off a scrape for a given source, dumps the raw data to S3, and chunks the catalog into batches of 25.
2. **`process_listings_batch_task`**: Receives a batch of items. It computes a SHA-256 hash of the state (`price` + `title` + `barcode`). **Optimization:** If the hash matches the database, it skips processing entirely!
3. **`normalize_and_persist_task`**: Only runs for new or updated items. It passes the raw item to the AI pipeline, deduplicates it against existing products, updates the database, and appends a `PriceHistory` log.

### 3. The AI Normalization Pipeline (`app/ai_pipeline.py`)
LLMs are expensive and can hallucinate. This system uses a **hybrid regex + AI approach** to ensure high quality and low latency:
* **Step 1 (Heuristics):** We pre-extract absolute values using Regex for `ml` (volume), `fragrance_type` (EDP/EDT), `gender`, and barcodes (`EAN-13`).
* **Step 2 (Short-circuit):** If the brand is provided by the source (e.g. Shopify vendor) and the heuristics found everything else, we skip the AI call entirely to save cost.
* **Step 3 (AI Inference):** If data is still missing, we prompt AWS Bedrock. Crucially, we *inject* our regex-extracted values as "Pre-confirmed hints" to lock the LLM into place and prevent it from overriding known facts.
* **Validation:** The AI's JSON output is strictly validated using Pydantic. If the LLM returns invalid JSON, the pipeline includes a 1-try self-correction loop.

### 4. Database Schema & Data Models (`app/models.py`)
* **`Product` (Canonical):** The master record. Uniquely identified by `(brand, product_name, variant, ml)`. One `Product` can have many `Listings`.
* **`Source`:** A configured target website (e.g., "Paris.cl" or "Wholesale Chile").
* **`ProductListing`:** The bridge. Represents a canonical `Product` as found on a specific `Source`. Holds the current state (URL, image, price, stock).
* **`PriceHistory`:** An append-only table. Every time a price or stock changes (detected via hash), a new row is added here, providing a full time-series.
* **`ScrapeLog`:** Audit log for tracing success, failures, and S3 backup links per scrape run.

---

## 🚀 Getting Started

### Prerequisites
* Python 3.13+
* PostgreSQL
* Redis Server
* AWS Account (S3 and Bedrock configured in your `.env`)

### Running the System
1. **Start the Database & Redis** (You can use Docker for Redis/Postgres).
2. **Start the API:**
   ```bash
   uvicorn app.main:app --reload
   ```
3. **Start the Celery Worker:**
   ```bash
   celery -A app.worker.celery_app worker --loglevel=info
   ```
4. **Trigger a Scrape:**
   Send a POST request to `/api/v1/trigger-scrape?source_id=<UUID>` or manage sources via the API.
