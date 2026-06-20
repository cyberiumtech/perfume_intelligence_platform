"""
WooCommerce BS4 scraper for WordPress/WooCommerce Chilean perfume sites.
Example target: lacasadelperfume.cl
"""
import logging
from typing import List, Dict, Any, Optional

import httpx
from bs4 import BeautifulSoup

from .base import BaseScraper

log = logging.getLogger(__name__)


class WooCommerceBs4Scraper(BaseScraper):
    """
    Scrapes WordPress + WooCommerce catalog pages using BeautifulSoup.
    Pagination: /tienda/page/N/
    """

    def __init__(self, base_url: str, source_id: str, config: Dict[str, Any] = None):
        super().__init__(base_url, source_id, config)
        self.catalog_path = self.config.get("catalog_path", "/tienda/")
        self.max_pages = int(self.config.get("max_pages", 50))

    async def extract_catalog(self) -> List[Dict[str, Any]]:
        """Extract all products from WooCommerce paginated catalog."""
        extracted_items: List[Dict[str, Any]] = []
        page = 1

        async with httpx.AsyncClient(
            timeout=45.0,
            headers=self._get_headers(),
            follow_redirects=True,
        ) as client:
            while page <= self.max_pages:
                if page == 1:
                    url = f"{self.base_url}{self.catalog_path}"
                else:
                    url = f"{self.base_url}{self.catalog_path}page/{page}/"

                try:
                    resp = await self._rate_limited_request(client, url)
                except Exception as e:
                    self._handle_network_error(e, f"page {page}")
                    break

                if resp.status_code == 404:
                    break
                if resp.status_code != 200:
                    log.warning(f"[WooCommerceBs4] Status {resp.status_code} on page {page}")
                    break

                soup = BeautifulSoup(resp.text, "html.parser")

                products = soup.select("li.product")
                if not products:
                    products = soup.select(".product-grid-item, .product-item, article.product")

                if not products:
                    log.info(f"[WooCommerceBs4] No products found on page {page} — stopping")
                    break

                for item in products:
                    try:
                        listing = self._parse_product(item)
                        if listing:
                            extracted_items.append(listing)
                    except Exception as e:
                        log.warning(f"[WooCommerceBs4] Parse error: {e}")
                        continue

                next_page = soup.select_one("a.next.page-numbers, .woocommerce-pagination .next")
                if not next_page:
                    break

                page += 1

        log.info(f"[WooCommerceBs4] Extracted {len(extracted_items)} listings from {self.base_url}")
        return extracted_items

    def _parse_product(self, item) -> Optional[Dict[str, Any]]:
        """Extract fields from a single WooCommerce product card."""
        try:
            # Title
            title_el = (
                item.select_one(".woocommerce-loop-product__title")
                or item.select_one(".product-title")
                or item.select_one("h2")
                or item.select_one("h3")
            )
            raw_title = self._clean_text(title_el.get_text()) if title_el else ""
            if not raw_title:
                return None

            # URL
            link_el = item.select_one("a.woocommerce-loop-product__link, a.product-image-link, h2 a, h3 a")
            product_url = link_el["href"] if link_el and link_el.get("href") else ""

            # Image
            img_el = item.select_one("img.attachment-woocommerce_thumbnail, img.wp-post-image, img")
            image_url = ""
            if img_el:
                image_url = (
                    img_el.get("data-src")
                    or img_el.get("data-lazy-src")
                    or img_el.get("src")
                    or ""
                )

            # Price
            price = 0.0
            price_el = item.select_one("ins .amount, .price .amount, .price bdi, .price")
            if price_el:
                price = self._parse_price(price_el.get_text())

            # Vendor/brand
            vendor = None
            category_el = item.select_one(".woodmart-product-cats a, .product-categories a")
            if category_el:
                vendor = self._clean_text(category_el.get_text())

            # Tags
            tags = []
            for cls in item.get("class", []):
                if cls.startswith("product_cat-"):
                    tags.append(cls.replace("product_cat-", "").replace("-", " "))

            # Description
            desc_el = item.select_one(
                ".woocommerce-product-details__short-description, .short-description, .excerpt"
            )
            description = self._clean_text(desc_el.get_text()) if desc_el else ""

            # Stock detection
            stock = None
            available = True
            stock_el = item.select_one(".stock, .quantity, .in-stock, .out-of-stock")
            if stock_el:
                stock_text = self._clean_text(stock_el.get_text())
                if self._detect_out_of_stock(stock_text):
                    available = False
                    stock = 0
                else:
                    import re
                    digits = re.sub(r"[^\d]", "", stock_text)
                    if digits:
                        stock = int(digits)

            return {
                "raw_title": raw_title,
                "vendor": vendor,
                "sku": None,
                "barcode": None,
                "price": price,
                "description": description,
                "url": product_url,
                "image_url": image_url,
                "tags": tags,
                "stock": stock,
                "available": available,
            }
        except Exception as e:
            log.warning(f"[WooCommerceBs4] Error parsing product: {e}")
            return None
