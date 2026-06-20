"""
Playwright-based scraper for ASP.NET B2B wholesale portals.

Features:
  - Session cookie persistence to /tmp/playwright_session_{source_id}.json
  - Cookie expiry detection → automatic re-login
  - CAPTCHA detection → CaptchaDetectedError, fail fast
  - Credentials from env vars B2B_EMAIL / B2B_PASSWORD, never hardcoded
  - Page Object Model pattern
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from .base import BaseScraper
from ..exceptions import AuthenticationError, CaptchaDetectedError, NetworkError

log = logging.getLogger(__name__)


def _parse_price(text: str) -> float:
    digits = re.sub(r"[^\d]", "", text or "")
    return float(digits) if digits else 0.0


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip())


class AuthPlaywrightScraper(BaseScraper):
    """
    Playwright scraper for password-protected B2B portals.

    Supports:
    - ASP.NET table layouts (Cosmetic Distribución, PDL)
    - Generic card/div layouts
    - Cookie persistence for session reuse
    - CAPTCHA detection and fail-fast
    """

    def __init__(self, base_url: str, source_id: str, config: Dict[str, Any] = None):
        super().__init__(base_url, source_id, config)
        self._session_file = Path(f"/tmp/playwright_session_{source_id}.json")

    async def extract_catalog(self) -> List[Dict[str, Any]]:
        """Extract catalog from a B2B portal requiring authentication."""
        try:
            from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
        except ImportError:
            log.error("[Playwright] playwright not installed. Run: pip install playwright && playwright install chromium")
            return []

        username = self.config.get("username") or os.getenv("B2B_EMAIL", "")
        password = self.config.get("password") or os.getenv("B2B_PASSWORD", "")

        if not username or not password:
            log.error(f"[Playwright] No credentials configured for {self.base_url}")
            return []

        login_url = self.config.get("login_url", "/WholeSale/Login")
        email_sel = self.config.get("email_selector", "#MainContent_txtEmail")
        pass_sel = self.config.get("password_selector", "#MainContent_txtPassword")
        submit_sel = self.config.get("submit_selector", "#MainContent_cmdLogin")
        catalog_url = self.config.get("catalog_url", "")
        product_sel = self.config.get("product_selector", ".product-item, .product-card, tr.product-row")
        next_sel = self.config.get("next_page_selector", "a.next, .pagination .next")
        max_pages = int(self.config.get("max_pages", 50))

        extracted_items: List[Dict[str, Any]] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)

            # Try to restore session cookies
            context = await self._create_context(browser, p)
            page = await context.new_page()

            try:
                # Check if session is still valid
                if not await self._is_session_valid(page, catalog_url, login_url):
                    # Login required
                    await self._perform_login(
                        page, login_url, username, password,
                        email_sel, pass_sel, submit_sel,
                    )
                    # Save cookies for next run
                    await self._save_session(context)

                log.info(f"[Playwright] Session active for {self.base_url}")

                # Navigate to catalog
                if catalog_url:
                    full_catalog = catalog_url if catalog_url.startswith("http") else f"{self.base_url}{catalog_url}"
                    await page.goto(full_catalog, timeout=30000)
                    await page.wait_for_load_state("networkidle", timeout=20000)

                # Detect CAPTCHA
                await self._check_captcha(page)

                # Detect layout type
                aspnet_table = await page.query_selector("table.table-hover")
                is_table_layout = aspnet_table is not None

                # Paginated extraction
                pages_scraped = 0
                while pages_scraped < max_pages:
                    await page.wait_for_load_state("domcontentloaded")

                    if is_table_layout:
                        items = await self._extract_table_page(page, pages_scraped)
                    else:
                        items = await self._extract_card_page(page, product_sel, pages_scraped)

                    if not items:
                        break

                    extracted_items.extend(items)
                    log.info(
                        f"[Playwright] Page {pages_scraped + 1}: {len(items)} items "
                        f"(total: {len(extracted_items)})"
                    )

                    # Navigate to next page
                    has_next = await self._navigate_next(page, is_table_layout, next_sel)
                    if not has_next:
                        break

                    pages_scraped += 1

            except CaptchaDetectedError:
                raise
            except AuthenticationError:
                raise
            except Exception as e:
                log.error(f"[Playwright] Extraction error for {self.base_url}: {e}")
            finally:
                await browser.close()

        log.info(f"[Playwright] Extracted {len(extracted_items)} listings from {self.base_url}")
        return extracted_items

    # ── Session Management ────────────────────────────────────────────────

    async def _create_context(self, browser, playwright):
        """Create browser context, restoring cookies if available."""
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        if self._session_file.exists():
            try:
                cookies = json.loads(self._session_file.read_text(encoding="utf-8"))
                context = await browser.new_context(user_agent=user_agent)
                await context.add_cookies(cookies)
                log.info(f"[Playwright] Restored session cookies from {self._session_file}")
                return context
            except Exception as e:
                log.warning(f"[Playwright] Failed to restore cookies: {e}")

        return await browser.new_context(user_agent=user_agent)

    async def _save_session(self, context) -> None:
        """Persist session cookies to disk."""
        try:
            cookies = await context.cookies()
            self._session_file.write_text(
                json.dumps(cookies, default=str),
                encoding="utf-8",
            )
            log.info(f"[Playwright] Saved session cookies to {self._session_file}")
        except Exception as e:
            log.warning(f"[Playwright] Failed to save cookies: {e}")

    async def _is_session_valid(self, page, catalog_url: str, login_url: str) -> bool:
        """Check if existing session cookies are still valid."""
        if not self._session_file.exists():
            return False

        try:
            target = catalog_url if catalog_url else login_url
            full_url = target if target.startswith("http") else f"{self.base_url}{target}"
            await page.goto(full_url, timeout=15000)
            current = page.url
            # If redirected to login page, session is expired
            if "login" in current.lower() or "Login" in current:
                log.info("[Playwright] Session expired — re-login required")
                return False
            return True
        except Exception:
            return False

    # ── Authentication ────────────────────────────────────────────────────

    async def _perform_login(
        self, page, login_url: str, username: str, password: str,
        email_sel: str, pass_sel: str, submit_sel: str,
    ) -> None:
        """Execute the login flow."""
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        full_login = login_url if login_url.startswith("http") else f"{self.base_url}{login_url}"
        await page.goto(full_login, timeout=30000)

        # Check for CAPTCHA before login
        await self._check_captcha(page)

        await page.wait_for_selector(email_sel, timeout=10000)
        await page.fill(email_sel, username)
        await page.fill(pass_sel, password)
        await page.click(submit_sel)

        try:
            await page.wait_for_url(
                lambda u: "Login" not in u and "login" not in u,
                timeout=15000,
            )
        except PlaywrightTimeout:
            raise AuthenticationError(
                f"Login failed for {self.base_url} — still on login page",
                source_id=self.source_id,
            )

        log.info(f"[Playwright] Login successful for {self.base_url}")

    # ── CAPTCHA Detection ─────────────────────────────────────────────────

    async def _check_captcha(self, page) -> None:
        """Detect CAPTCHA challenges and fail fast."""
        captcha_selectors = [
            "iframe[src*='recaptcha']",
            "iframe[src*='hcaptcha']",
            ".g-recaptcha",
            "#captcha",
            "[class*='captcha']",
        ]
        for sel in captcha_selectors:
            el = await page.query_selector(sel)
            if el:
                raise CaptchaDetectedError(
                    f"CAPTCHA detected on {page.url}",
                    source_id=self.source_id,
                )

        # Check page text for CAPTCHA keywords
        body_text = await page.inner_text("body")
        if body_text and "no soy un robot" in body_text.lower():
            raise CaptchaDetectedError(
                f"CAPTCHA text detected on {page.url}",
                source_id=self.source_id,
            )

    # ── Table Layout Extraction ───────────────────────────────────────────

    async def _extract_table_page(self, page, page_num: int) -> List[Dict[str, Any]]:
        """Extract products from an ASP.NET table layout."""
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        try:
            await page.wait_for_selector("table.table-hover tbody tr", timeout=10000)
        except PlaywrightTimeout:
            log.warning(f"[Playwright] Table rows not found on page {page_num + 1}")
            return []

        rows = await page.query_selector_all("table.table-hover tbody tr")
        if not rows:
            return []

        items = []
        for row in rows:
            try:
                listing = await self._parse_table_row(row)
                if listing:
                    items.append(listing)
            except Exception as e:
                log.warning(f"[Playwright] Table row parse error: {e}")
                continue

        return items

    async def _parse_table_row(self, row) -> Optional[Dict[str, Any]]:
        """
        Parse a single <tr> from an ASP.NET table.

        Expected columns:
          TD[0]=Brand, TD[1]=SKU, TD[2]=Image, TD[3]=Name,
          TD[4]=Price(tax), TD[5]=Net price, TD[6]=Stock
        """
        try:
            tds = await row.query_selector_all("td")
            if len(tds) < 5:
                return None

            brand = _clean(await tds[0].inner_text()) if len(tds) > 0 else ""

            sku_text = _clean(await tds[1].inner_text()) if len(tds) > 1 else ""
            barcode = None
            sku = sku_text
            ean_match = re.match(r"^(\d{13})(.+)$", sku_text)
            if ean_match:
                barcode = self._validate_ean(ean_match.group(1))
                sku = ean_match.group(2)

            image_url = ""
            if len(tds) > 2:
                img_el = await tds[2].query_selector("img")
                if img_el:
                    image_url = (
                        await img_el.get_attribute("data-src")
                        or await img_el.get_attribute("src")
                        or ""
                    )

            raw_name = ""
            if len(tds) > 3:
                raw_name = _clean(await tds[3].inner_text())
                raw_name = re.sub(r"\d+\s*por\s*caja\s*$", "", raw_name).strip()

            raw_title = f"{brand} {raw_name}".strip() if brand and raw_name else (raw_name or brand)
            if not raw_title:
                return None

            price = _parse_price(await tds[4].inner_text()) if len(tds) > 4 else 0.0
            net_price = _parse_price(await tds[5].inner_text()) if len(tds) > 5 else 0.0
            final_price = net_price if net_price > 0 else price

            stock = None
            available = True
            if len(tds) > 6:
                stock_text = _clean(await tds[6].inner_text())
                if self._detect_out_of_stock(stock_text):
                    available = False
                    stock = 0
                else:
                    digits = re.sub(r"[^\d]", "", stock_text)
                    if digits:
                        stock = int(digits)
                        if stock == 0:
                            available = False

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
                "available": available,
            }
        except Exception as e:
            log.warning(f"[Playwright] Table row parse error: {e}")
            return None

    # ── Card Layout Extraction ────────────────────────────────────────────

    async def _extract_card_page(
        self, page, product_sel: str, page_num: int
    ) -> List[Dict[str, Any]]:
        """Extract products from a generic card/div layout."""
        items = []
        for sel in product_sel.split(","):
            sel = sel.strip()
            elements = await page.query_selector_all(sel)
            if elements:
                for el in elements:
                    try:
                        listing = await self._parse_card_element(el, page)
                        if listing:
                            items.append(listing)
                    except Exception as e:
                        log.warning(f"[Playwright] Card parse error: {e}")
                        continue
                break
        return items

    async def _parse_card_element(self, item, page) -> Optional[Dict[str, Any]]:
        """Parse a single product card element."""
        try:
            raw_title = ""
            for title_sel in [".product-title", ".product-name", "td.name", "h3", "h4", "a.name"]:
                el = await item.query_selector(title_sel)
                if el:
                    raw_title = _clean(await el.inner_text())
                    break

            if not raw_title:
                raw_title = _clean(await item.inner_text())
                if len(raw_title) > 200 or not raw_title:
                    return None

            price = 0.0
            for price_sel in [".price", ".price-value", "td.price", ".product-price", "span.monto"]:
                el = await item.query_selector(price_sel)
                if el:
                    price = _parse_price(await el.inner_text())
                    break

            product_url = ""
            link_el = await item.query_selector("a")
            if link_el:
                href = await link_el.get_attribute("href") or ""
                product_url = href if href.startswith("http") else f"{self.base_url}{href}"

            image_url = ""
            img_el = await item.query_selector("img")
            if img_el:
                image_url = (
                    await img_el.get_attribute("data-src")
                    or await img_el.get_attribute("src")
                    or ""
                )

            sku = await item.get_attribute("data-sku") or await item.get_attribute("data-code") or None
            barcode_raw = await item.get_attribute("data-barcode") or await item.get_attribute("data-ean") or None
            barcode = self._validate_ean(barcode_raw)

            description = ""
            for desc_sel in [".description", ".short-description", ".details", "td.desc"]:
                desc_el = await item.query_selector(desc_sel)
                if desc_el:
                    description = _clean(await desc_el.inner_text())
                    break

            stock = None
            available = True
            for stock_sel in [".stock", ".quantity", "td.stock", ".availability"]:
                el = await item.query_selector(stock_sel)
                if el:
                    stock_text = _clean(await el.inner_text())
                    if self._detect_out_of_stock(stock_text):
                        available = False
                        stock = 0
                    else:
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
                "available": available,
            }
        except Exception as e:
            log.warning(f"[Playwright] Element parse error: {e}")
            return None

    # ── Pagination ────────────────────────────────────────────────────────

    async def _navigate_next(self, page, is_table_layout: bool, next_sel: str) -> bool:
        """Navigate to the next page. Returns False if no next page."""
        try:
            if is_table_layout:
                return await self._navigate_aspnet_next(page, next_sel)
            else:
                return await self._navigate_generic_next(page, next_sel)
        except Exception as e:
            log.warning(f"[Playwright] Pagination failed: {e}")
            return False

    async def _navigate_aspnet_next(self, page, next_sel: str) -> bool:
        """Handle ASP.NET postback pagination."""
        next_btn = await page.query_selector("a#MainContent_lbNext")
        if not next_btn:
            for sel in next_sel.split(","):
                next_btn = await page.query_selector(sel.strip())
                if next_btn:
                    break
        if not next_btn:
            return False

        href = await next_btn.get_attribute("href") or ""
        postback_match = re.search(r"__doPostBack\('([^']+)'", href)

        if postback_match:
            target = postback_match.group(1)
            await page.evaluate(f"__doPostBack('{target}', '')")
        else:
            await next_btn.click(force=True)

        await asyncio.sleep(1)
        try:
            await page.wait_for_selector(
                "#MainContent_updateProgress", state="hidden", timeout=20000
            )
        except Exception:
            pass
        await page.wait_for_load_state("networkidle", timeout=20000)
        await asyncio.sleep(1)
        return True

    async def _navigate_generic_next(self, page, next_sel: str) -> bool:
        """Handle standard link-based pagination."""
        next_btn = await page.query_selector(next_sel)
        if not next_btn:
            return False
        await next_btn.click()
        await page.wait_for_load_state("networkidle", timeout=15000)
        return True
