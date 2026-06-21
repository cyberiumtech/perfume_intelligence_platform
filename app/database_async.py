"""
Asynchronous database configuration exclusively for the FastAPI application.
Replaces psycopg2 with asyncpg to maximize concurrency for web requests.
"""
import os
import logging

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

load_dotenv()

log = logging.getLogger(__name__)

# Modify the synchronous DATABASE_URL to use asyncpg
raw_url = os.getenv("DATABASE_URL")
if not raw_url:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set. "
        "Set it in your .env file or environment."
    )

if raw_url.startswith("postgresql://"):
    ASYNC_DATABASE_URL = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
else:
    ASYNC_DATABASE_URL = raw_url

if "localhost" in ASYNC_DATABASE_URL or "127.0.0.1" in ASYNC_DATABASE_URL:
    log.warning("Using localhost async database URL — ensure this is intentional in production")

# Create Async Engine
engine_async = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=40,
)

# Create Async Session Factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine_async,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

async def get_db_async():
    """FastAPI dependency that yields an asynchronous database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
