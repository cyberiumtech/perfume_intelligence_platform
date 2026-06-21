"""
PostgreSQL-based scrape queue worker.

Replaces Celery + Redis with a simple polling loop against the scrape_queue table.
Processes one job at a time with retry logic.
Includes auto-scheduling to replace Celery Beat — periodically queues scrapes
for active sources that haven't been scraped recently.

Usage:
    python -m app.scraper_worker
"""
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from sqlalchemy import text, func

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
SCRAPE_INTERVAL_HOURS = int(os.getenv("SCRAPE_INTERVAL_HOURS", "4"))  # auto-schedule interval

# Graceful shutdown flag
_shutdown_requested = False


def _handle_signal(signum, frame):
    """Signal handler for graceful shutdown."""
    global _shutdown_requested
    sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
    log.info(f"Received {sig_name} — shutting down after current job completes...")
    _shutdown_requested = True


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


# ══════════════════════════════════════════════════════════════════════════════
# AUTO-SCHEDULING (replaces Celery Beat)
# ══════════════════════════════════════════════════════════════════════════════

def _auto_schedule_sources(db) -> int:
    """
    Queue scrapes for active sources that haven't been scraped recently.

    Checks each active source's last successful scrape log. If older than
    SCRAPE_INTERVAL_HOURS, inserts a PENDING entry in scrape_queue.

    Returns the number of sources queued.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=SCRAPE_INTERVAL_HOURS)
    sources = db.query(Source).filter(Source.is_active == True).all()
    queued = 0

    for source in sources:
        # Skip if already pending in queue
        existing_pending = db.query(ScrapeQueue).filter(
            ScrapeQueue.source_id == source.id,
            ScrapeQueue.status.in_(["PENDING", "RUNNING"]),
        ).first()
        if existing_pending:
            continue

        # Check last successful scrape
        last_scrape = db.query(ScrapeLog).filter(
            ScrapeLog.source_id == source.id,
            ScrapeLog.status.in_([ScrapeStatus.SUCCESS, ScrapeStatus.PARTIAL]),
        ).order_by(ScrapeLog.started_at.desc()).first()

        if last_scrape and last_scrape.started_at and last_scrape.started_at > cutoff:
            continue  # Scraped recently — skip

        # Queue a new scrape
        entry = ScrapeQueue(source_id=source.id, priority=5)
        db.add(entry)
        queued += 1
        log.info(f"Auto-scheduled scrape for: {source.name}")

    if queued > 0:
        db.commit()

    return queued


def run_worker():
    """Main worker loop — polls scrape_queue for pending jobs."""
    global _shutdown_requested

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info(
        f"Scraper worker started "
        f"(poll_interval={POLL_INTERVAL}s, "
        f"auto_schedule_interval={SCRAPE_INTERVAL_HOURS}h, "
        f"batch_size={BATCH_SIZE})"
    )

    last_schedule_check = 0.0
    schedule_check_interval = 300  # Check auto-schedule every 5 minutes

    while not _shutdown_requested:
        db = SessionLocal()
        try:
            # Periodically auto-schedule sources (replaces Celery Beat)
            now = time.time()
            if now - last_schedule_check > schedule_check_interval:
                try:
                    queued = _auto_schedule_sources(db)
                    if queued > 0:
                        log.info(f"Auto-scheduled {queued} source(s) for scraping")
                except Exception as e:
                    log.error(f"Auto-scheduling failed: {e}", exc_info=True)
                last_schedule_check = now

            job = claim_next_job(db)
            if job:
                log.info(f"Claimed job {job.id} for source {job.source_id}")
                db.close()
                process_job(job)
            else:
                db.close()
                # Sleep in small increments to check shutdown flag
                for _ in range(POLL_INTERVAL):
                    if _shutdown_requested:
                        break
                    time.sleep(1)
        except Exception as e:
            log.error(f"Worker loop error: {e}", exc_info=True)
            try:
                db.close()
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)

    log.info("Worker shut down gracefully.")


if __name__ == "__main__":
    run_worker()
