"""
BS4 Scrapers for non-Shopify Chilean perfume distributors.

Two implementations:
1. WooCommerceBs4Scraper  — lacasadelperfume.cl (WordPress/WooCommerce)
2. JumpsellerBs4Scraper   — multimarcasmayorista.cl (Jumpseller platform)

Both scrape paginated HTML catalog pages using httpx + BeautifulSoup4.
"""
import re
import httpx
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from .base import BaseScraper


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
}


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip())


def _parse_price(text: str) -> float:
    """Parse Chilean price strings like '$34.990' or '34990' → 34990.0"""
    digits = re.sub(r"[^\d]", "", text or "")
    return float(digits) if digits else 0.0


# ---------------------------------------------------------------------------
# WooCommerce Scraper — lacasadelperfume.cl
# ---------------------------------------------------------------------------

class WooCommerceBs4Scraper(BaseScraper):
    """
    Scrapes lacasadelperfume.cl which runs WordPress + WooCommerce.
    Catalog URL pattern: /tienda/page/N/
    Products rendered as standard WooCommerce li.product blocks.
    """

    def __init__(self, base_url: str, source_id: str, config: Dict[str, Any] = None):
        super().__init__(base_url, source_id, config)
        # Configurable catalog path — default for lacasadelperfume.cl
        self.catalog_path = config.get("catalog_path", "/tienda/") if config else "/tienda/"
        self.max_pages = config.get("max_pages", 50) if config else 50

    async def extract_catalog(self) -> List[Dict[str, Any]]:
        extracted_items: List[Dict[str, Any]] = []
        page = 1

        async with httpx.AsyncClient(
            timeout=45.0, headers=_HEADERS, follow_redirects=True
        ) as client:
            while page <= self.max_pages:
                # WooCommerce pagination: /tienda/ (page 1), /tienda/page/2/, etc.
                if page == 1:
                    url = f"{self.base_url}{self.catalog_path}"
                else:
                    url = f"{self.base_url}{self.catalog_path}page/{page}/"

                try:
                    resp = await client.get(url)
                except httpx.RequestError as e:
                    print(f"[WooCommerceBs4] Network error on page {page}: {e}")
                    break

                if resp.status_code == 404:
                    break  # Exceeded last page
                if resp.status_code != 200:
                    print(f"[WooCommerceBs4] Status {resp.status_code} on page {page}")
                    break

                soup = BeautifulSoup(resp.text, "html.parser")

                # WooCommerce standard: ul.products > li.product
                products = soup.select("li.product")
                if not products:
                    # Try alternate selector used by some themes
                    products = soup.select(".product-grid-item, .product-item, article.product")

                if not products:
                    print(f"[WooCommerceBs4] No products found on page {page} — stopping")
                    break

                for item in products:
                    listing = self._parse_product(item)
                    if listing:
                        extracted_items.append(listing)

                # Check for next page link
                next_page = soup.select_one("a.next.page-numbers, .woocommerce-pagination .next")
                if not next_page:
                    break

                page += 1

        print(f"[WooCommerceBs4] Extracted {len(extracted_items)} listings from {self.base_url}")
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
            raw_title = _clean(title_el.get_text()) if title_el else ""
            if not raw_title:
                return None

            # Product page URL
            link_el = item.select_one("a.woocommerce-loop-product__link, a.product-image-link, h2 a, h3 a")
            product_url = link_el["href"] if link_el and link_el.get("href") else ""

            # Image
            img_el = item.select_one("img.attachment-woocommerce_thumbnail, img.wp-post-image, img")
            image_url = ""
            if img_el:
                # WooCommerce lazy-loads: src may be placeholder, data-src is real
                image_url = (
                    img_el.get("data-src")
                    or img_el.get("data-lazy-src")
                    or img_el.get("src")
                    or ""
                )

            # Price — WooCommerce wraps in <bdi> or <span class="amount">
            price = 0.0
            price_el = item.select_one("ins .amount, .price .amount, .price bdi, .price")
            if price_el:
                price = _parse_price(price_el.get_text())

            # Category/vendor from breadcrumbs or data attributes
            vendor = None
            category_el = item.select_one(".woodmart-product-cats a, .product-categories a")
            if category_el:
                vendor = _clean(category_el.get_text())

            # Tags from data attributes
            tags = []
            tag_str = item.get("class", [])
            for cls in tag_str:
                if cls.startswith("product_cat-"):
                    tags.append(cls.replace("product_cat-", "").replace("-", " "))

            # Extract description from catalog card if present
            desc_el = item.select_one(".woocommerce-product-details__short-description, .short-description, .excerpt, .product-excerpt")
            description = _clean(desc_el.get_text()) if desc_el else ""

            # Stock
            stock = None
            stock_el = item.select_one(".stock, .quantity, .in-stock")
            if stock_el:
                stock_text = _clean(stock_el.get_text())
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
                "available": True,
            }
        except Exception as e:
            print(f"[WooCommerceBs4] Error parsing product: {e}")
            return None


# ---------------------------------------------------------------------------
# Jumpseller Scraper — multimarcasmayorista.cl
# ---------------------------------------------------------------------------

class JumpsellerBs4Scraper(BaseScraper):
    """
    Scrapes multimarcasmayorista.cl which runs on the Jumpseller SaaS platform.
    Catalog URL pattern: /perfumes?page=N
    Jumpseller renders product grids with specific CSS classes.
    """

    def __init__(self, base_url: str, source_id: str, config: Dict[str, Any] = None):
        super().__init__(base_url, source_id, config)
        self.catalog_path = config.get("catalog_path", "/perfumes") if config else "/perfumes"
        self.max_pages = config.get("max_pages", 100) if config else 100

    async def extract_catalog(self) -> List[Dict[str, Any]]:
        extracted_items: List[Dict[str, Any]] = []
        page = 1

        async with httpx.AsyncClient(
            timeout=45.0, headers=_HEADERS, follow_redirects=True
        ) as client:
            while page <= self.max_pages:
                url = f"{self.base_url}{self.catalog_path}?page={page}"
                try:
                    resp = await client.get(url)
                except httpx.RequestError as e:
                    print(f"[JumpsellerBs4] Network error on page {page}: {e}")
                    break

                if resp.status_code == 404:
                    break
                if resp.status_code != 200:
                    print(f"[JumpsellerBs4] Status {resp.status_code} on page {page}")
                    break

                soup = BeautifulSoup(resp.text, "html.parser")

                # Jumpseller typical selectors
                products = (
                    soup.select(".product-item")
                    or soup.select(".item-product")
                    or soup.select(".product-grid .item")
                    or soup.select("article.product")
                    or soup.select(".product-block")
                )

                if not products:
                    print(f"[JumpsellerBs4] No products on page {page} — stopping")
                    break

                page_items = 0
                for item in products:
                    listing = self._parse_product(item)
                    if listing:
                        extracted_items.append(listing)
                        page_items += 1

                # Jumpseller pagination: check for a next page link
                next_link = soup.select_one("a[rel='next'], .pagination .next a, li.next a")
                if not next_link:
                    break

                page += 1

        print(f"[JumpsellerBs4] Extracted {len(extracted_items)} listings from {self.base_url}")
        return extracted_items

    def _parse_product(self, item) -> Optional[Dict[str, Any]]:
        """Extract fields from a Jumpseller product card."""
        try:
            # Title
            title_el = (
                item.select_one(".product-title, .item-name, h3.title, h2.title, .name, h4 a, h4")
            )
            raw_title = _clean(title_el.get_text()) if title_el else ""
            if not raw_title:
                return None

            # URL
            link_el = item.select_one("a.product-image-wrapper, a.product-title-link, a")
            product_url = ""
            if link_el and link_el.get("href"):
                href = link_el["href"]
                if href.startswith("http"):
                    product_url = href
                else:
                    product_url = f"{self.base_url}{href}"

            # Image
            img_el = item.select_one("img.product-image, img.item-image, img")
            image_url = ""
            if img_el:
                image_url = (
                    img_el.get("data-src")
                    or img_el.get("src")
                    or ""
                )

            # Price — Jumpseller: span.money or .price or .product-block-normal
            price = 0.0
            price_el = item.select_one(".price .money, .price, .product-price, span.money, .product-block-normal")
            if price_el:
                price = _parse_price(price_el.get_text())

            # Vendor — Jumpseller may expose brand in a .brand or .vendor element
            vendor = None
            vendor_el = item.select_one(".brand, .vendor, .product-brand")
            if vendor_el:
                vendor = _clean(vendor_el.get_text()) or None

            # Tags from class names
            tags = []
            sku_el = item.select_one("[data-sku], [data-product-sku]")
            sku = sku_el.get("data-sku") or sku_el.get("data-product-sku") if sku_el else None

            # Description / short text
            desc_el = item.select_one(".description, .short-description, .product-description, .caption")
            description = _clean(desc_el.get_text()) if desc_el else ""

            # Stock
            stock = None
            stock_el = item.select_one(".stock, .quantity, .inventory")
            if stock_el:
                stock_text = _clean(stock_el.get_text())
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
                "tags": tags,
                "stock": stock,
                "available": True,
            }
        except Exception as e:
            print(f"[JumpsellerBs4] Error parsing product: {e}")
            return None
