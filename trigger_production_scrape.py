import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app.database import SessionLocal
from app.models import Source
from app.worker import orchestrate_scrape_task

def run_production_scrape():
    db = SessionLocal()
    
    # Exclude the test source
    active_sources = db.query(Source).filter(
        Source.is_active == True,
        Source.name.not_like("%[TEST]%")
    ).all()
    
    if not active_sources:
        print("No active sources found! Check seed_sources.py")
        return
        
    print(f"Found {len(active_sources)} active production sources.")
    print("Queueing full scrapes to Celery...\n")
    
    for source in active_sources:
        print(f" -> Queueing: {source.name} ({source.base_url})")
        # .delay() pushes it to the Redis queue for the Celery worker to process
        # Notice we are NOT passing a 'limit' parameter here, so it will scrape everything!
        orchestrate_scrape_task.delay(str(source.id))
        
    print("\nAll tasks queued successfully!")
    print("If you haven't already, you must start your Celery worker in a new terminal:")
    print("    .\\.venv\\Scripts\\celery -A app.worker worker --loglevel=info -P gevent")
    
    db.close()

if __name__ == "__main__":
    run_production_scrape()
