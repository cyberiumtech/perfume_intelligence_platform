"""
Shopify scraper using the /products.json cursor-paginated API.

No JavaScript required. Extracts the full rich product payload.
Key insight: Shopify's `vendor` field = the perfume brand name.
"""
import logging
from typing import List, Dict, Any

import httpx

from .base import BaseScraper

log = logging.getLogger(__name__)


class ShopifyScraper(BaseScraper):
    """
    Scrapes Shopify stores via /products.json API.
    Handles cursor pagination (250 items per page).
    """

    async def extract_catalog(self) -> List[Dict[str, Any]]:
        """Extract all products from a Shopify store."""
        page = 1
        extracted_items: List[Dict[str, Any]] = []
        max_pages = int(self.config.get("max_pages", 100))

        async with httpx.AsyncClient(
            timeout=30.0,
            headers=self._get_headers(),
            follow_redirects=True,
        ) as client:
            while page <= max_pages:
                catalog_path = self.config.get("catalog_path", "/products.json")
                separator = "&" if "?" in catalog_path else "?"
                url = f"{self.base_url}{catalog_path}{separator}limit=250&page={page}"

                try:
                    response = await self._rate_limited_request(client, url)
                except Exception as e:
                    self._handle_network_error(e, f"page {page}")
                    break

                if response.status_code == 404:
                    log.info(f"[ShopifyScraper] /products.json returned 404 for {self.base_url}")
                    break
                if response.status_code != 200:
                    log.warning(f"[ShopifyScraper] Status {response.status_code} on page {page}")
                    break

                try:
                    data = response.json()
                except Exception:
                    log.error(f"[ShopifyScraper] Invalid JSON on page {page}")
                    break

                products = data.get("products", [])
                if not products:
                    break

                for product in products:
                    try:
                        items = self._parse_product(product)
                        extracted_items.extend(items)
                    except Exception as e:
                        log.warning(f"[ShopifyScraper] Parse error: {e}")
                        continue

                page += 1

        log.info(f"[ShopifyScraper] Extracted {len(extracted_items)} listings from {self.base_url}")
        return extracted_items

    def _parse_product(self, product: dict) -> List[Dict[str, Any]]:
        """Parse a single Shopify product into one or more listings (per variant)."""
        items = []

        raw_title = product.get("title", "").strip()
        raw_body = self._strip_html(product.get("body_html", "") or "")
        vendor = product.get("vendor", "").strip() or None
        handle = product.get("handle", "")
        tags = [t.strip() for t in product.get("tags", []) if t.strip()]
        product_url = f"{self.base_url}/products/{handle}" if handle else ""

        images = product.get("images", [])
        image_url = images[0].get("src", "") if images else ""

        variants = product.get("variants", [])
        if not variants:
            return items

        for variant in variants:
            try:
                price_str = variant.get("price", "0") or "0"
                price = float(price_str) if price_str else 0.0

                barcode_raw = (variant.get("barcode") or "").strip() or None
                barcode = self._validate_ean(barcode_raw)
                sku = (variant.get("sku") or "").strip() or None
                available = variant.get("available", True)

                qty = variant.get("inventory_quantity")
                stock = int(qty) if qty is not None else None

                variant_title = (variant.get("title") or "").strip()
                full_title = raw_title
                if variant_title and variant_title.lower() != "default title":
                    full_title = f"{raw_title} - {variant_title}"

                items.append({
                    "raw_title": full_title,
                    "vendor": vendor,
                    "sku": sku,
                    "barcode": barcode,
                    "price": price,
                    "description": raw_body,
                    "url": product_url,
                    "image_url": image_url,
                    "tags": tags,
                    "stock": stock,
                    "available": available,
                })
            except Exception as e:
                log.warning(f"[ShopifyScraper] Variant parse error: {e}")
                continue

        return items
