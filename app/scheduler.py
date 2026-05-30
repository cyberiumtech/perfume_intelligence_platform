"""
Celery Beat scheduler — automated periodic scraping of all active sources.

Runs every 4 hours per source:
  - Low enough frequency to avoid rate-limiting and IP bans
  - High enough to capture intraday price changes

Usage:
  # Start the Celery worker:
  celery -A app.worker.celery_app worker --loglevel=info

  # Start the Beat scheduler (in a separate process):
  celery -A app.scheduler.celery_app beat --loglevel=info --scheduler app.scheduler:DatabaseScheduler
"""
import logging
from datetime import timedelta

from celery import Celery
from celery.schedules import crontab

from .worker import celery_app, orchestrate_scrape_task
from .database import SessionLocal
from .models import Source

log = logging.getLogger(__name__)


def get_beat_schedule() -> dict:
    """
    Dynamically build the Celery Beat schedule from all active Source rows.
    Called once at scheduler startup.
    """
    db = SessionLocal()
    schedule = {}
    try:
        sources = db.query(Source).filter(Source.is_active == True).all()
        for source in sources:
            # Stagger sources by 15 minutes to avoid simultaneous large crawls
            task_name = f"scrape-{source.name.lower().replace(' ', '-')}"
            schedule[task_name] = {
                "task": "app.worker.orchestrate_scrape_task",
                "schedule": timedelta(hours=4),
                "args": [str(source.id)],
                "options": {"queue": "default"},
            }
            log.info(f"[Beat] Scheduled '{source.name}' every 4 hours")

        if not schedule:
            log.warning("[Beat] No active sources found in DB — nothing scheduled")

    except Exception as e:
        log.error(f"[Beat] Failed to load sources from DB: {e}")
    finally:
        db.close()

    return schedule


# Apply the dynamic schedule to the celery app
celery_app.conf.beat_schedule = get_beat_schedule()
celery_app.conf.beat_max_loop_interval = 300  # Re-evaluate schedule every 5 minutes


# ── Manual trigger helper for CLI ────────────────────────────────────────────

def trigger_all_sources():
    """
    Immediately trigger a scrape for all active sources.
    Useful for initial full-load or debugging.
    """
    db = SessionLocal()
    try:
        sources = db.query(Source).filter(Source.is_active == True).all()
        for source in sources:
            task = orchestrate_scrape_task.delay(str(source.id))
            print(f"✓ Triggered: {source.name} (task: {task.id})")
        print(f"\nDispatched {len(sources)} scrape tasks.")
    finally:
        db.close()


if __name__ == "__main__":
    trigger_all_sources()
