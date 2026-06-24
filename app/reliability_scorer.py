"""
Source Reliability Scoring Background Task

Calculates and updates reliability scores for all active sources on a weekly basis.
Can be run as a standalone script or integrated into a scheduler.

Usage:
    python -m app.reliability_scorer
"""
import logging
import os
from datetime import datetime, timezone
from typing import Dict

from dotenv import load_dotenv
from sqlalchemy import select, func

from .database import SessionLocal
from .models import Source, ProductListing, SourceReliabilityScore, StockConfidence

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def calculate_source_reliability() -> Dict[str, int]:
    """
    Calculate and update reliability scores for all active sources.

    Scoring heuristic:
    - Base score: 50
    - Stock accuracy: +50 points (% of listings with HIGH or MEDIUM confidence)
    - Fulfillment rate: Not yet implemented (requires actual order data)

    Returns:
        Dict with source_id → new_reliability_score
    """
    db = SessionLocal()
    results = {}

    try:
        # Get all active sources
        sources = db.query(Source).filter(Source.is_active == True).all()
        log.info(f"Calculating reliability scores for {len(sources)} active sources")

        current_month = datetime.now(timezone.utc).strftime("%Y-%m")

        for source in sources:
            # Get all listings for this source
            listings = db.query(ProductListing).filter(
                ProductListing.source_id == source.id
            ).all()

            total_listings = len(listings)
            if total_listings == 0:
                log.info(f"[{source.name}] No listings — keeping default score 50")
                continue

            # Count listings with verifiable stock data (HIGH or MEDIUM confidence)
            high_confidence = sum(
                1 for l in listings
                if l.stock_confidence in (StockConfidence.HIGH, StockConfidence.MEDIUM)
            )

            # Calculate stock accuracy as % of listings with verifiable stock
            stock_accuracy = high_confidence / total_listings if total_listings > 0 else 0.0

            # Update source reliability score (base 50 + stock_accuracy * 50)
            new_score = int(50 + (stock_accuracy * 50))
            previous_score = source.reliability_score
            source.reliability_score = new_score

            log.info(
                f"[{source.name}] Reliability: {previous_score} → {new_score} "
                f"(stock_accuracy={stock_accuracy:.2%}, {high_confidence}/{total_listings} verifiable)"
            )

            results[str(source.id)] = new_score

            # Record historical reliability score
            existing_score = db.query(SourceReliabilityScore).filter(
                SourceReliabilityScore.source_id == source.id,
                SourceReliabilityScore.month == current_month,
            ).first()

            if existing_score:
                # Update existing month record
                existing_score.avg_stock_accuracy = stock_accuracy
                existing_score.fulfillment_rate = 0.0  # Placeholder
            else:
                # Create new month record
                monthly_score = SourceReliabilityScore(
                    source_id=source.id,
                    month=current_month,
                    avg_stock_accuracy=stock_accuracy,
                    fulfillment_rate=0.0,  # Placeholder until we have order data
                )
                db.add(monthly_score)

        db.commit()
        log.info(f"Reliability scoring complete. Updated {len(results)} sources.")
        return results

    except Exception as e:
        db.rollback()
        log.error(f"Reliability scoring failed: {e}", exc_info=True)
        raise
    finally:
        db.close()


def main():
    """CLI entry point for standalone execution."""
    log.info("Starting source reliability scoring task...")
    try:
        results = calculate_source_reliability()
        log.info(f"Success! Scored {len(results)} sources.")
    except Exception as e:
        log.error(f"Task failed: {e}")
        exit(1)


if __name__ == "__main__":
    main()
