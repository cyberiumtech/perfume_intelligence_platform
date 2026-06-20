import sys
import logging
from app.database import SessionLocal
from app.models import Source, ScrapeQueue
from app.scraper_worker import claim_next_job, process_job

# Set up clean logging to terminal
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cli_scraper")

def run_all_now():
    db = SessionLocal()
    
    # 1. Fetch active sources
    sources = db.query(Source).filter(Source.is_active == True).all()
    if not sources:
        log.warning("No active sources found in the database. Exiting.")
        db.close()
        return

    log.info(f"Found {len(sources)} active sources. Queueing them for immediate scrape...")

    # 2. Add them all to the queue
    for source in sources:
        # Check if already pending
        existing = db.query(ScrapeQueue).filter(
            ScrapeQueue.source_id == source.id, 
            ScrapeQueue.status == "PENDING"
        ).first()
        
        if not existing:
            entry = ScrapeQueue(source_id=source.id, priority=10) # High priority
            db.add(entry)
            
    db.commit()

    # 3. Process the queue until empty
    log.info("Starting worker to process the queue until it is empty...\n" + "-"*50)
    
    jobs_processed = 0
    while True:
        # We need a fresh session for the lock
        db_job = SessionLocal()
        job = claim_next_job(db_job)
        if not job:
            db_job.close()
            break
            
        log.info(f"-> Processing job {job.id} for source {job.source_id}")
        db_job.close()  # process_job manages its own sessions internally
        
        process_job(job)
        jobs_processed += 1
        print("-" * 50)

    log.info(f"Finished! Processed {jobs_processed} scrape jobs successfully.")
    db.close()

if __name__ == "__main__":
    run_all_now()
