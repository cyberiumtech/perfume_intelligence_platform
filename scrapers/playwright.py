"""
Playwright-based scraper for ASP.NET B2B wholesale portals.

Targets:
  - pdlbodega.cl/wholesale/ (Productos de Lujo VIP)
  - cosmetic-distribucion.cl/WholeSale/Login

Both use ASP.NET WebForms with __VIEWSTATE, requiring session cookies
preserved through the login flow. Playwright handles this natively.

Credentials must be in the Source.config JSONB column:
  {"username": "...", "password": "...", "catalog_url": "..."}
"""
import asyncio
import os
import re
from typing import List, Dict, Any, Optional
from .base import BaseScraper


_HEADERS_LIST = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _parse_price(text: str) -> float:
    digits = re.sub(r"[^\d]", "", text or "")
    return float(digits) if digits else 0.0


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip())


class AuthPlaywrightScraper(BaseScraper):
    """
    Generic Playwright scraper for password-protected B2B portals.
    Reads login credentials from Source.config:
      - username / password: login credentials
      - login_url: e.g. /WholeSale/Login or /wholesale/
      - email_selector / password_selector: CSS selectors for login form fields
      - submit_selector: CSS selector for submit button
      - catalog_url: URL of first catalog page after login
      - product_selector: CSS selector for product cards
      - next_page_selector: CSS selector for next-page link/button
    """

    async def extract_catalog(self) -> List[Dict[str, Any]]:
        try:
            from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
        except ImportError:
            print("[Playwright] playwright not installed. Run: uv add playwright && playwright install chromium")
            return []

        username = self.config.get("username") or os.getenv("B2B_EMAIL", "")
        password = self.config.get("password") or os.getenv("B2B_PASSWORD", "")

        if not username or not password:
            print(f"[Playwright] No credentials configured for {self.base_url} — skipping")
            return []

        login_url = self.config.get("login_url", "/WholeSale/Login")
        email_sel = self.config.get("email_selector", "#MainContent_txtEmail")
        pass_sel = self.config.get("password_selector", "#MainContent_txtPassword")
        submit_sel = self.config.get("submit_selector", "#MainContent_cmdLogin")
        catalog_url = self.config.get("catalog_url", "")
        product_sel = self.config.get("product_selector", ".product-item, .product-card, tr.product-row")
        next_sel = self.config.get("next_page_selector", "a.next, .pagination .next")
        max_pages = self.config.get("max_pages", 50)

        extracted_items: List[Dict[str, Any]] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=_HEADERS_LIST)
            page = await context.new_page()

            try:
                # ── 1. Login ─────────────────────────────────────────────
                await page.goto(f"{self.base_url}{login_url}", timeout=30000)
                await page.wait_for_selector(email_sel, timeout=10000)
                await page.fill(email_sel, username)
                await page.fill(pass_sel, password)
                await page.click(submit_sel)

                # Wait for successful redirect away from login page
                try:
                    await page.wait_for_url(
                        lambda u: "Login" not in u and "login" not in u,
                        timeout=15000
                    )
                except PlaywrightTimeout:
                    print(f"[Playwright] Login may have failed for {self.base_url}")
                    await browser.close()
                    return []

                print(f"[Playwright] Login successful for {self.base_url}")

                # ── 2. Navigate to catalog ────────────────────────────────
                if catalog_url:
                    full_catalog = catalog_url if catalog_url.startswith("http") else f"{self.base_url}{catalog_url}"
                    await page.goto(full_catalog, timeout=30000)
                    await page.wait_for_load_state("networkidle", timeout=20000)

                # ── 3. Detect page layout ─────────────────────────────────
                # Check if the page uses an ASP.NET table layout
                aspnet_table = await page.query_selector("table.table-hover")
                is_table_layout = aspnet_table is not None

                # ── 4. Paginated extraction ──────────────────────────────
                pages_scraped = 0
                while pages_scraped < max_pages:
                    await page.wait_for_load_state("domcontentloaded")

                    if is_table_layout:
                        # ── ASP.NET table layout ──────────────────────────
                        # Wait for table to be present after postback
                        try:
                            await page.wait_for_selector("table.table-hover tbody tr", timeout=10000)
                        except PlaywrightTimeout:
                            print(f"[Playwright] Table rows not found on page {pages_scraped + 1}")
                            break

                        rows = await page.query_selector_all("table.table-hover tbody tr")
                        if not rows:
                            print(f"[Playwright] No product rows on page {pages_scraped + 1}")
                            break

                        for row in rows:
                            listing = await self._parse_table_row(row)
                            if listing:
                                extracted_items.append(listing)

                        print(f"[Playwright] Page {pages_scraped + 1}: {len(rows)} rows extracted (total: {len(extracted_items)})")

                        # ASP.NET postback pagination — look for "Siguiente" or next link
                        next_btn = await page.query_selector("a#MainContent_lbNext")
                        if not next_btn:
                            # Fallback: try generic next selectors
                            for sel in next_sel.split(","):
                                next_btn = await page.query_selector(sel.strip())
                                if next_btn:
                                    break
                        if not next_btn:
                            print(f"[Playwright] No next page button found — pagination complete")
                            break

                        try:
                            # Extract the __doPostBack target from the href attribute
                            href = await next_btn.get_attribute("href") or ""
                            postback_match = re.search(r"__doPostBack\('([^']+)'", href)

                            if postback_match:
                                # Trigger postback via JavaScript — bypasses overlay interception
                                postback_target = postback_match.group(1)
                                await page.evaluate(f"__doPostBack('{postback_target}', '')")
                            else:
                                # Fallback: force-click (ignore overlay)
                                await next_btn.click(force=True)

                            # Wait for the ASP.NET UpdatePanel to finish loading
                            await asyncio.sleep(1)
                            try:
                                await page.wait_for_selector(
                                    "#MainContent_updateProgress",
                                    state="hidden",
                                    timeout=20000
                                )
                            except Exception:
                                pass
                            await page.wait_for_load_state("networkidle", timeout=20000)
                            await asyncio.sleep(1)  # Extra buffer for DOM update
                        except Exception as e:
                            print(f"[Playwright] Pagination failed: {e}")
                            break

                    else:
                        # ── Generic card/div layout ───────────────────────
                        items = []
                        for sel in product_sel.split(","):
                            sel = sel.strip()
                            items = await page.query_selector_all(sel)
                            if items:
                                break

                        if not items:
                            print(f"[Playwright] No products found with selector '{product_sel}' on page {pages_scraped + 1}")
                            break

                        for item in items:
                            listing = await self._parse_element(item, page)
                            if listing:
                                extracted_items.append(listing)

                        # Next page
                        next_btn = await page.query_selector(next_sel)
                        if not next_btn:
                            break

                        try:
                            await next_btn.click()
                            await page.wait_for_load_state("networkidle", timeout=15000)
                        except Exception:
                            break

                    pages_scraped += 1

            except Exception as e:
                print(f"[Playwright] Extraction error for {self.base_url}: {e}")
            finally:
                await browser.close()

        print(f"[Playwright] Extracted {len(extracted_items)} listings from {self.base_url}")
        return extracted_items

    async def _parse_table_row(self, row) -> Optional[Dict[str, Any]]:
        """
        Parse a single <tr> from an ASP.NET Repeater product table.

        Expected column layout (Cosmetic Distribución / similar portals):
          TD[0] = Brand (Marca)
          TD[1] = SKU
          TD[2] = Image (Imagen)
          TD[3] = Name + description (Nombre)
          TD[4] = Price with tax (Precio)
          TD[5] = Net price (Neto)
          TD[6] = Stock
          TD[7] = Quantity input (Cantidad) — ignored
          TD[8] = Total CLP — ignored
          TD[9] = Notes (Observación) — ignored
        """
        try:
            tds = await row.query_selector_all("td")
            if len(tds) < 5:
                return None  # Not a product row (e.g. header or spacer)

            # Brand
            brand = _clean(await tds[0].inner_text()) if len(tds) > 0 else ""

            # SKU — may contain EAN + internal code concatenated
            sku_text = _clean(await tds[1].inner_text()) if len(tds) > 1 else ""

            # Try to split EAN (13 digits) from the SKU code
            barcode = None
            sku = sku_text
            ean_match = re.match(r"^(\d{13})(.+)$", sku_text)
            if ean_match:
                barcode = ean_match.group(1)
                sku = ean_match.group(2)

            # Image
            image_url = ""
            if len(tds) > 2:
                img_el = await tds[2].query_selector("img")
                if img_el:
                    image_url = (
                        await img_el.get_attribute("data-src")
                        or await img_el.get_attribute("src")
                        or ""
                    )

            # Name — strip trailing "0 por caja" or similar notes
            raw_name = ""
            if len(tds) > 3:
                raw_name = _clean(await tds[3].inner_text())
                # Remove trailing stock-per-box note like "0 por caja"
                raw_name = re.sub(r"\d+\s*por\s*caja\s*$", "", raw_name).strip()

            # Build raw_title combining brand + name for the AI normalizer
            raw_title = f"{brand} {raw_name}".strip() if brand and raw_name else (raw_name or brand)

            if not raw_title:
                return None

            # Price (with tax)
            price = 0.0
            if len(tds) > 4:
                price = _parse_price(await tds[4].inner_text())

            # Net price (without tax) — use as primary if available
            net_price = 0.0
            if len(tds) > 5:
                net_price = _parse_price(await tds[5].inner_text())

            # Use net price if available, otherwise gross price
            final_price = net_price if net_price > 0 else price

            # Stock
            stock = None
            if len(tds) > 6:
                stock_text = _clean(await tds[6].inner_text())
                digits = re.sub(r"[^\d]", "", stock_text)
                if digits:
                    stock = int(digits)

            return {
                "raw_title": raw_title,
                "vendor": brand,
                "sku": sku if sku else None,
                "barcode": barcode,
                "price": final_price,
                "description": "",
                "url": "",
                "image_url": image_url,
                "tags": [],
                "stock": stock,
                "available": True,
            }
        except Exception as e:
            print(f"[Playwright] Table row parse error: {e}")
            return None

    async def _parse_element(self, item, page) -> Optional[Dict[str, Any]]:
        """Parse a single product element from the page DOM (generic card/div layout)."""
        try:
            # Try multiple title selectors
            raw_title = ""
            for title_sel in [".product-title", ".product-name", "td.name", "h3", "h4", "a.name"]:
                el = await item.query_selector(title_sel)
                if el:
                    raw_title = _clean(await el.inner_text())
                    break

            if not raw_title:
                # Fall back to entire element text (table row etc.)
                raw_title = _clean(await item.inner_text())
                if len(raw_title) > 200 or not raw_title:
                    return None

            # Price
            price = 0.0
            for price_sel in [".price", ".price-value", "td.price", ".product-price", "span.monto"]:
                el = await item.query_selector(price_sel)
                if el:
                    price = _parse_price(await el.inner_text())
                    break

            # URL
            product_url = ""
            link_el = await item.query_selector("a")
            if link_el:
                href = await link_el.get_attribute("href") or ""
                product_url = href if href.startswith("http") else f"{self.base_url}{href}"

            # Image
            image_url = ""
            img_el = await item.query_selector("img")
            if img_el:
                image_url = (
                    await img_el.get_attribute("data-src")
                    or await img_el.get_attribute("src")
                    or ""
                )

            # SKU / barcode from data attributes
            sku = await item.get_attribute("data-sku") or await item.get_attribute("data-code") or None
            barcode = await item.get_attribute("data-barcode") or await item.get_attribute("data-ean") or None

            # Description
            description = ""
            for desc_sel in [".description", ".short-description", ".details", "td.desc"]:
                desc_el = await item.query_selector(desc_sel)
                if desc_el:
                    description = _clean(await desc_el.inner_text())
                    break

            # Stock
            stock = None
            for stock_sel in [".stock", ".quantity", "td.stock", ".availability"]:
                el = await item.query_selector(stock_sel)
                if el:
                    stock_text = _clean(await el.inner_text())
                    digits = re.sub(r"[^\d]", "", stock_text)
                    if digits:
                        stock = int(digits)
                    break

            return {
                "raw_title": raw_title,
                "vendor": None,
                "sku": sku,
                "barcode": barcode,
                "price": price,
                "description": description,
                "url": product_url,
                "image_url": image_url,
                "tags": [],
                "stock": stock,
                "available": True,
            }
        except Exception as e:
            print(f"[Playwright] Element parse error: {e}")
            return None