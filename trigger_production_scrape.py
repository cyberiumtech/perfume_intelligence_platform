"""
Trigger a production scrape for all active sources via the scrape_queue.

Replaces the old Celery-based trigger. Now inserts entries into the
PostgreSQL scrape_queue table which the scraper_worker polls.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app.database import SessionLocal
from app.models import Source, ScrapeQueue


def run_production_scrape():
    db = SessionLocal()

    # Exclude test sources
    active_sources = db.query(Source).filter(
        Source.is_active == True,
        Source.name.not_like("%[TEST]%")
    ).all()

    if not active_sources:
        print("No active sources found! Check seed_sources.py")
        return

    print(f"Found {len(active_sources)} active production sources.")
    print("Queueing scrapes to PostgreSQL scrape_queue...\n")

    queued = 0
    for source in active_sources:
        # Check if already pending
        existing = db.query(ScrapeQueue).filter(
            ScrapeQueue.source_id == source.id,
            ScrapeQueue.status == "PENDING"
        ).first()

        if existing:
            print(f"  [SKIP] {source.name} — already pending in queue")
            continue

        entry = ScrapeQueue(source_id=source.id, priority=10)
        db.add(entry)
        print(f"  [QUEUED] {source.name} ({source.base_url})")
        queued += 1

    db.commit()

    print(f"\n{queued} sources queued successfully!")
    print("Make sure the scraper worker is running:")
    print("    python -m app.scraper_worker")

    db.close()


if __name__ == "__main__":
    run_production_scrape()
