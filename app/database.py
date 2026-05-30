# app/database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Connects to the local PostgreSQL spun up via Docker
SQLALCHEMY_DATABASE_URL = "postgresql://postgres:root@127.0.0.1:5432/perfume_db"

# pool_pre_ping=True ensures the connection is alive before routing queries, 
# preventing silent drops during long background scrapes.
engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()