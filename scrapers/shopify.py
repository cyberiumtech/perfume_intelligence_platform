import re
import httpx
from typing import List, Dict, Any
from .base import BaseScraper


class ShopifyScraper(BaseScraper):
    """
    Shopify scraper using the /products.json cursor-paginated API.
    No JavaScript required. Extracts the full rich product payload.
    
    Key insight: Shopify's `vendor` field = the perfume brand name.
    This allows the AI pipeline to skip brand inference entirely.
    """

    async def extract_catalog(self) -> List[Dict[str, Any]]:
        page = 1
        extracted_items = []

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
            while True:
                catalog_path = self.config.get("catalog_path", "/products.json")
                if "?" in catalog_path:
                    url = f"{self.base_url}{catalog_path}&limit=250&page={page}"
                else:
                    url = f"{self.base_url}{catalog_path}?limit=250&page={page}"
                try:
                    response = await client.get(url)
                except httpx.RequestError as e:
                    print(f"[ShopifyScraper] Network error on page {page}: {e}")
                    break

                if response.status_code == 404:
                    # Some Shopify stores disable the API — break gracefully
                    print(f"[ShopifyScraper] /products.json returned 404 for {self.base_url}")
                    break
                if response.status_code != 200:
                    print(f"[ShopifyScraper] Unexpected status {response.status_code} on page {page}")
                    break

                try:
                    data = response.json()
                except Exception:
                    break

                products = data.get("products", [])
                if not products:
                    break  # Exhausted all pages

                for product in products:
                    raw_title = product.get("title", "").strip()
                    raw_body = self._strip_html(product.get("body_html", "") or "")
                    vendor = product.get("vendor", "").strip() or None
                    handle = product.get("handle", "")
                    tags = [t.strip() for t in product.get("tags", []) if t.strip()]
                    product_url = f"{self.base_url}/products/{handle}" if handle else ""

                    # Primary image URL
                    images = product.get("images", [])
                    image_url = images[0].get("src", "") if images else ""

                    variants = product.get("variants", [])
                    if not variants:
                        continue

                    for variant in variants:
                        price_str = variant.get("price", "0") or "0"
                        price = float(price_str) if price_str else 0.0

                        barcode = (variant.get("barcode") or "").strip() or None
                        sku = (variant.get("sku") or "").strip() or None
                        available = variant.get("available", True)

                        # Inventory quantity (may be None if not tracked)
                        qty = variant.get("inventory_quantity")
                        stock = int(qty) if qty is not None else None

                        # Per-variant title suffix (e.g., "100ml" or "Default Title")
                        variant_title = (variant.get("title") or "").strip()
                        full_title = raw_title
                        if variant_title and variant_title.lower() != "default title":
                            full_title = f"{raw_title} - {variant_title}"

                        # Extract MOQ and bulk pricing from metafields if available
                        moq = None
                        bulk_tiers = []
                        # Note: Shopify doesn't expose MOQ via products.json
                        # This would require the Admin API or custom metafields

                        extracted_items.append({
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
                            "inventory_quantity": stock,  # Explicit for stock confidence detection
                            "available": available,
                            "moq": moq,
                            "bulk_tiers": bulk_tiers,
                        })

                page += 1

        print(f"[ShopifyScraper] Extracted {len(extracted_items)} listings from {self.base_url}")
        return extracted_items

    @staticmethod
    def _strip_html(html: str) -> str:
        """Remove HTML tags and collapse whitespace."""
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text
