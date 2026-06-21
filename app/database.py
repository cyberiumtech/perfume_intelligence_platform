"""
Database connection configuration for the Perfume Intelligence Platform.

Reads DATABASE_URL from environment — NEVER hardcodes credentials.
"""
import os
import logging

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

log = logging.getLogger(__name__)

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

if not SQLALCHEMY_DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set. "
        "Set it in your .env file or environment. Example: "
        "DATABASE_URL=postgresql://user:password@localhost:5432/perfume_intelligence_db"
    )

if "localhost" in SQLALCHEMY_DATABASE_URL or "127.0.0.1" in SQLALCHEMY_DATABASE_URL:
    log.warning("Using localhost database URL — ensure this is intentional in production")

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency that yields a database session and closes it after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()