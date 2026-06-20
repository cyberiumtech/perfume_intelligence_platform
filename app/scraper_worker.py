"""
PostgreSQL-based scrape queue worker.

Replaces Celery + Redis with a simple polling loop against the scrape_queue table.
Processes one job at a time with retry logic.

Usage:
    python -m app.scraper_worker
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import text

from .database import SessionLocal
from .delta_engine import DeltaEngine, transition_delisted
from .models import Source, ScrapeLog, ScrapeQueue, ScrapeStatus
from .scrapers.factory import ScraperFactory

load_dotenv()

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv("SCRAPER_POLL_INTERVAL", "30"))  # seconds
BATCH_SIZE = int(os.getenv("SCRAPER_BATCH_SIZE", "25"))
DELIST_THRESHOLD_MINUTES = int(os.getenv("DELIST_THRESHOLD_MINUTES", "1440"))  # 24h


def claim_next_job(db) -> ScrapeQueue | None:
    """
    Atomically claim the next pending job from the queue.

    Uses SELECT ... FOR UPDATE SKIP LOCKED to prevent concurrent workers
    from claiming the same job.
    """
    result = db.execute(
        text("""
            UPDATE scrape_queue
            SET status = 'RUNNING', started_at = now()
            WHERE id = (
                SELECT id FROM scrape_queue
                WHERE status = 'PENDING'
                  AND scheduled_at <= now()
                ORDER BY priority DESC, scheduled_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id
        """)
    ).fetchone()

    if result is None:
        return None

    db.commit()
    return db.query(ScrapeQueue).filter(ScrapeQueue.id == result[0]).first()


def process_job(job: ScrapeQueue) -> None:
    """
    Execute a scrape job:
    1. Load source configuration
    2. Run the appropriate scraper
    3. Process results through the delta engine
    4. Update scrape log and queue status
    """
    db = SessionLocal()
    scrape_log = None

    try:
        source = db.query(Source).filter(Source.id == job.source_id).first()
        if not source:
            log.error(f"Source not found: {job.source_id}")
            _fail_job(db, job, "Source not found")
            return

        if not source.is_active:
            log.info(f"Source {source.name} is inactive — skipping")
            _complete_job(db, job, "CANCELLED")
            return

        # Create scrape log
        scrape_log = ScrapeLog(
            source_id=source.id,
            status=ScrapeStatus.STARTED,
        )
        db.add(scrape_log)
        db.commit()
        db.refresh(scrape_log)

        log.info(f"Starting scrape: {source.name} ({source.engine_type})")

        # Run scraper
        scraper = ScraperFactory.get_scraper(
            source.engine_type.value if hasattr(source.engine_type, 'value') else source.engine_type,
            source.base_url,
            str(source.id),
            source.config,
        )
        raw_catalog = asyncio.run(scraper.extract_catalog())

        total = len(raw_catalog)
        log.info(f"[{source.name}] Extracted {total} raw listings")

        # Store raw data in scrape_log (replaces S3)
        try:
            scrape_log.raw_data = raw_catalog
        except Exception as e:
            log.warning(f"Failed to store raw data: {e}")

        scrape_log.records_extracted = total

        # Process through delta engine in batches
        total_updated = 0
        total_skipped = 0
        total_failed = 0

        for i in range(0, total, BATCH_SIZE):
            batch = raw_catalog[i:i + BATCH_SIZE]
            batch_db = SessionLocal()
            try:
                engine = DeltaEngine(batch_db)
                result = engine.process_batch(batch, str(source.id))
                total_updated += result["records_updated"]
                total_skipped += result["records_skipped"]
                total_failed += result["records_failed"]
            except Exception as e:
                log.error(f"Batch {i // BATCH_SIZE + 1} failed: {e}", exc_info=True)
                total_failed += len(batch)
            finally:
                batch_db.close()

        # Transition stale listings to DELISTED
        delist_db = SessionLocal()
        try:
            delisted_count = transition_delisted(
                delist_db, str(source.id), DELIST_THRESHOLD_MINUTES
            )
            if delisted_count > 0:
                log.info(f"Delisted {delisted_count} stale listings for {source.name}")
        except Exception as e:
            log.warning(f"Delist transition failed: {e}")
        finally:
            delist_db.close()

        # Update scrape log
        scrape_log.records_updated = total_updated
        scrape_log.records_skipped = total_skipped
        scrape_log.records_failed = total_failed
        scrape_log.status = ScrapeStatus.SUCCESS if total_failed == 0 else ScrapeStatus.PARTIAL
        scrape_log.ended_at = datetime.now(timezone.utc)
        db.commit()

        # Complete job
        _complete_job(db, job, "DONE")

        log.info(
            f"Scrape complete: {source.name} — "
            f"extracted={total}, updated={total_updated}, "
            f"skipped={total_skipped}, failed={total_failed}"
        )

    except Exception as exc:
        db.rollback()
        error_msg = str(exc)[:1000]

        if scrape_log:
            scrape_log.status = ScrapeStatus.FAIL
            scrape_log.error_message = error_msg
            scrape_log.ended_at = datetime.now(timezone.utc)
            db.commit()

        # Handle retries
        if job.retry_count < job.max_retries:
            job.retry_count += 1
            job.status = "PENDING"
            job.error_message = error_msg
            job.started_at = None
            db.commit()
            log.warning(f"Job {job.id} will retry ({job.retry_count}/{job.max_retries}): {error_msg}")
        else:
            _fail_job(db, job, error_msg)
            log.error(f"Job {job.id} permanently failed: {error_msg}")

    finally:
        db.close()


def _complete_job(db, job: ScrapeQueue, status: str) -> None:
    """Mark a job as completed."""
    job.status = status
    job.ended_at = datetime.now(timezone.utc)
    db.commit()


def _fail_job(db, job: ScrapeQueue, error_msg: str) -> None:
    """Mark a job as permanently failed."""
    job.status = "FAILED"
    job.error_message = error_msg[:1000]
    job.ended_at = datetime.now(timezone.utc)
    db.commit()


def run_worker():
    """Main worker loop — polls scrape_queue for pending jobs."""
    log.info(f"Scraper worker started (poll interval: {POLL_INTERVAL}s)")

    while True:
        db = SessionLocal()
        try:
            job = claim_next_job(db)
            if job:
                log.info(f"Claimed job {job.id} for source {job.source_id}")
                db.close()
                process_job(job)
            else:
                db.close()
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            log.info("Worker shutting down...")
            db.close()
            break
        except Exception as e:
            log.error(f"Worker loop error: {e}", exc_info=True)
            db.close()
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run_worker()
