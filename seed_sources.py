import os
import sys
import uuid
import sqlalchemy
from dotenv import load_dotenv

load_dotenv('.env')
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://postgres:root@localhost:5432/perfume_intelligence_db')
engine = sqlalchemy.create_engine(DATABASE_URL)

SOURCES = [
    {
        "name": "Elite Perfumes",
        "base_url": "https://www.eliteperfumes-distribuidor.cl/",
        "engine_type": "shopify",
        "config": {}
    },
    {
        "name": "La Casa del Perfume",
        "base_url": "https://www.lacasadelperfume.com/",
        "engine_type": "shopify",
        "config": {}
    },
    {
        "name": "Comprar en Chile",
        "base_url": "https://comprarenchile.cl/",
        "engine_type": "shopify",
        "config": {}
    },
    {
        "name": "Multimarcas Mayorista",
        "base_url": "https://www.multimarcasmayorista.cl/",
        "engine_type": "bs4_jumpseller",
        "config": {}
    },
    {
        "name": "VYP Mayorista",
        "base_url": "https://vypmayorista.cl/",
        "engine_type": "bs4_woocommerce",
        "config": {}
    },
    {
        "name": "PDL Bodega",
        "base_url": "https://www.pdlbodega.cl/",
        "engine_type": "playwright",
        "config": {
            "username": "kameshgurkha1991.kg@gmail.com",
            "password": "123456",
            "login_url": "/wholesale"
        }
    },
    {
        "name": "Cosmetic Distribucion",
        "base_url": "https://www.cosmetic-distribucion.cl/",
        "engine_type": "playwright",
        "config": {
            "username": "kameshgurkha1991.kg@gmail.com",
            "password": "Kunal1986@@",
            "login_url": "/wholesale/Login.aspx"
        }
    }
]

import json

def seed():
    with engine.begin() as conn:
        for s in SOURCES:
            # Check if source exists
            res = conn.execute(sqlalchemy.text("SELECT id FROM sources WHERE name = :name"), {"name": s["name"]}).fetchone()
            if res:
                # Update existing
                print(f"Updating {s['name']}...")
                conn.execute(
                    sqlalchemy.text("""
                        UPDATE sources 
                        SET base_url = :base_url, engine_type = :engine_type, config = CAST(:config AS JSONB), is_active = true
                        WHERE name = :name
                    """),
                    {
                        "name": s["name"],
                        "base_url": s["base_url"],
                        "engine_type": s["engine_type"],
                        "config": json.dumps(s["config"])
                    }
                )
            else:
                # Insert new
                print(f"Inserting {s['name']}...")
                conn.execute(
                    sqlalchemy.text("""
                        INSERT INTO sources (id, name, base_url, engine_type, config, is_active, currency, created_at)
                        VALUES (:id, :name, :base_url, :engine_type, CAST(:config AS JSONB), true, 'CLP', NOW())
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "name": s["name"],
                        "base_url": s["base_url"],
                        "engine_type": s["engine_type"],
                        "config": json.dumps(s["config"])
                    }
                )
    print("Seed complete.")

if __name__ == "__main__":
    seed()
