"""
Source seeding script - registers all Chilean perfume distributor sources.

Run once after fresh DB creation:
    python seed_sources.py

Sources:
1. eliteperfumes-distribuidor.cl  → Shopify (public API, no credentials needed)
2. lacasadelperfume.cl            → WooCommerce/WordPress BS4 scraper
3. multimarcasmayorista.cl        → Jumpseller BS4 scraper
4. pdlbodega.cl                   → ASP.NET B2B (Playwright, needs credentials)
5. cosmetic-distribucion.cl       → ASP.NET B2B (Playwright, needs credentials)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from app.database import SessionLocal, engine
from app.models import Base, Source

# Ensure tables exist
Base.metadata.create_all(bind=engine)

SOURCES = [
    # ── Source 1: Elite Perfumes Distribuidor (Shopify) ─────────────────────
    {
        "name": "Elite Perfumes Distribuidor",
        "base_url": "https://www.eliteperfumes-distribuidor.cl",
        "engine_type": "shopify",
        "config": {
            "catalog_path": "/collections/perfumes/products.json"
        },
        "is_active": True,
    },

    # ── Source 2: La Casa del Perfume (WooCommerce) ──────────────────────────
    {
        "name": "La Casa del Perfume",
        "base_url": "https://lacasadelperfume.cl",
        "engine_type": "bs4_woocommerce",
        "config": {
            "catalog_path": "/tienda/",
            "max_pages": 50,
        },
        "is_active": True,
    },

    # ── Source 3: Multimarcas Mayorista (Jumpseller) ─────────────────────────
    {
        "name": "Multimarcas Mayorista",
        "base_url": "https://www.multimarcasmayorista.cl",
        "engine_type": "bs4_jumpseller",
        "config": {
            "catalog_path": "/perfumes",
            "max_pages": 100,
        },
        "is_active": True,
    },

    # ── Source 4: PDL Bodega / Productos de Lujo VIP (ASP.NET B2B) ──────────
    # Requires B2B login credentials. Set is_active=False until credentials added.
    {
        "name": "PDL Bodega (Productos de Lujo VIP)",
        "base_url": "https://pdlbodega.cl",
        "engine_type": "playwright",
        "config": {
            "login_url": "/wholesale/",
            "email_selector": "#MainContent_txtEmail",
            "password_selector": "#MainContent_txtPassword",
            "submit_selector": "#MainContent_cmdLogin",
            "catalog_url": "/CreateOrder",
            "product_selector": "tr.product-row, .product-item, tr[data-product]",
            "next_page_selector": "a.next, .pagination .next, li.next a",
            "max_pages": 100,
            # Set credentials here or in .env as B2B_PDL_EMAIL / B2B_PDL_PASSWORD
            "username": os.getenv("B2B_PDL_EMAIL", ""),
            "password": os.getenv("B2B_PDL_PASSWORD", ""),
        },
        # Set to True once you have valid B2B credentials for this portal
        "is_active": False,
    },

    # ── Source 5: Cosmetic Distribucion (ASP.NET B2B) ────────────────────────
    # Requires B2B login credentials. Set is_active=False until credentials added.
    {
        "name": "Cosmetic Distribucion",
        "base_url": "https://cosmetic-distribucion.cl",
        "engine_type": "playwright",
        "config": {
            "login_url": "/WholeSale/Login",
            "email_selector": "#MainContent_txtEmail",
            "password_selector": "#MainContent_txtPassword",
            "submit_selector": "#MainContent_cmdLogin",
            "catalog_url": "/WholeSale/Catalog",
            "product_selector": ".product-item, tr.product-row, .catalog-item",
            "next_page_selector": "a.next, .pagination .next",
            "max_pages": 100,
            # Set credentials here or in .env as B2B_COSMETIC_EMAIL / B2B_COSMETIC_PASSWORD
            "username": os.getenv("B2B_COSMETIC_EMAIL", ""),
            "password": os.getenv("B2B_COSMETIC_PASSWORD", ""),
        },
        # Set to True once you have valid B2B credentials for this portal
        "is_active": False,
    },
]


def seed():
    db = SessionLocal()
    created, skipped, updated = 0, 0, 0

    try:
        for source_data in SOURCES:
            is_active = source_data.pop("is_active")
            existing = db.query(Source).filter(Source.name == source_data["name"]).first()

            if existing:
                # Update config and active status
                for k, v in source_data.items():
                    setattr(existing, k, v)
                existing.is_active = is_active
                print(f"  [U] Updated:  {existing.name}")
                updated += 1
            else:
                source = Source(**source_data, is_active=is_active)
                db.add(source)
                print(f"  [+] Created:  {source.name} ({source.engine_type})")
                created += 1

        db.commit()
        print(f"\nSeeding complete - {created} created, {updated} updated, {skipped} skipped")
        print("\nActive sources:")
        for s in db.query(Source).filter(Source.is_active == True).all():
            print(f"  [ON]  {s.name} [{s.engine_type}] - {s.base_url}")
        print("\nInactive sources (need B2B credentials):")
        for s in db.query(Source).filter(Source.is_active == False).all():
            print(f"  [OFF] {s.name} [{s.engine_type}] - add credentials to .env to activate")

    except Exception as e:
        db.rollback()
        print(f"[ERROR] Seeding failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print("Seeding Chilean perfume distributor sources...")
    print("")
    seed()
