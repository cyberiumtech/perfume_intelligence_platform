"""
Jumpseller BS4 scraper for Chilean perfume distributors on the Jumpseller platform.
Example target: multimarcasmayorista.cl
"""
import logging
import re
from typing import List, Dict, Any, Optional

import httpx
from bs4 import BeautifulSoup

from .base import BaseScraper

log = logging.getLogger(__name__)


class JumpsellerBs4Scraper(BaseScraper):
    """
    Scrapes Jumpseller SaaS platform catalog pages using BeautifulSoup.
    Pagination: /perfumes?page=N
    """

    def __init__(self, base_url: str, source_id: str, config: Dict[str, Any] = None):
        super().__init__(base_url, source_id, config)
        self.catalog_path = self.config.get("catalog_path", "/perfumes")
        self.max_pages = int(self.config.get("max_pages", 100))

    async def extract_catalog(self) -> List[Dict[str, Any]]:
        """Extract all products from Jumpseller paginated catalog."""
        extracted_items: List[Dict[str, Any]] = []
        page = 1

        async with httpx.AsyncClient(
            timeout=45.0,
            headers=self._get_headers(),
            follow_redirects=True,
        ) as client:
            while page <= self.max_pages:
                url = f"{self.base_url}{self.catalog_path}?page={page}"

                try:
                    resp = await self._rate_limited_request(client, url)
                except Exception as e:
                    self._handle_network_error(e, f"page {page}")
                    break

                if resp.status_code == 404:
                    break
                if resp.status_code != 200:
                    log.warning(f"[JumpsellerBs4] Status {resp.status_code} on page {page}")
                    break

                soup = BeautifulSoup(resp.text, "html.parser")

                products = (
                    soup.select(".product-item")
                    or soup.select(".item-product")
                    or soup.select(".product-grid .item")
                    or soup.select("article.product")
                    or soup.select(".product-block")
                )

                if not products:
                    log.info(f"[JumpsellerBs4] No products on page {page} — stopping")
                    break

                for item in products:
                    try:
                        listing = self._parse_product(item)
                        if listing:
                            extracted_items.append(listing)
                    except Exception as e:
                        log.warning(f"[JumpsellerBs4] Parse error: {e}")
                        continue

                next_link = soup.select_one("a[rel='next'], .pagination .next a, li.next a")
                if not next_link:
                    break

                page += 1

        log.info(f"[JumpsellerBs4] Extracted {len(extracted_items)} listings from {self.base_url}")
        return extracted_items

    def _parse_product(self, item) -> Optional[Dict[str, Any]]:
        """Extract fields from a Jumpseller product card."""
        try:
            # Title
            title_el = item.select_one(
                ".product-title, .item-name, h3.title, h2.title, .name, h4 a, h4"
            )
            raw_title = self._clean_text(title_el.get_text()) if title_el else ""
            if not raw_title:
                return None

            # URL
            link_el = item.select_one("a.product-image-wrapper, a.product-title-link, a")
            product_url = ""
            if link_el and link_el.get("href"):
                href = link_el["href"]
                product_url = href if href.startswith("http") else f"{self.base_url}{href}"

            # Image
            img_el = item.select_one("img.product-image, img.item-image, img")
            image_url = ""
            if img_el:
                image_url = img_el.get("data-src") or img_el.get("src") or ""

            # Price
            price = 0.0
            price_el = item.select_one(
                ".price .money, .price, .product-price, span.money, .product-block-normal"
            )
            if price_el:
                price = self._parse_price(price_el.get_text())

            # Vendor
            vendor = None
            vendor_el = item.select_one(".brand, .vendor, .product-brand")
            if vendor_el:
                vendor = self._clean_text(vendor_el.get_text()) or None

            # SKU
            sku_el = item.select_one("[data-sku], [data-product-sku]")
            sku = None
            if sku_el:
                sku = sku_el.get("data-sku") or sku_el.get("data-product-sku")

            # Description
            desc_el = item.select_one(".description, .short-description, .product-description, .caption")
            description = self._clean_text(desc_el.get_text()) if desc_el else ""

            # Stock detection
            stock = None
            available = True
            stock_el = item.select_one(".stock, .quantity, .inventory")
            if stock_el:
                stock_text = self._clean_text(stock_el.get_text())
                if self._detect_out_of_stock(stock_text):
                    available = False
                    stock = 0
                else:
                    digits = re.sub(r"[^\d]", "", stock_text)
                    if digits:
                        stock = int(digits)

            return {
                "raw_title": raw_title,
                "vendor": vendor,
                "sku": sku,
                "barcode": None,
                "price": price,
                "description": description,
                "url": product_url,
                "image_url": image_url,
                "tags": [],
                "stock": stock,
                "available": available,
            }
        except Exception as e:
            log.warning(f"[JumpsellerBs4] Error parsing product: {e}")
            return None
