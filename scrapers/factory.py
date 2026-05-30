from typing import Dict, Any
from .base import BaseScraper
from .shopify import ShopifyScraper
from .beautifulsoup import WooCommerceBs4Scraper, JumpsellerBs4Scraper
from .playwright import AuthPlaywrightScraper


class ScraperFactory:
    """
    Factory that maps Source.engine_type → concrete scraper strategy.

    Supported engine types:
      - "shopify"           → ShopifyScraper       (products.json API)
      - "bs4_woocommerce"   → WooCommerceBs4Scraper (WordPress/WooCommerce HTML)
      - "bs4_jumpseller"    → JumpsellerBs4Scraper  (Jumpseller SaaS HTML)
      - "playwright"        → AuthPlaywrightScraper  (B2B login + JS-rendered)
    """

    @staticmethod
    def get_scraper(
        engine_type: str,
        base_url: str,
        source_id: str,
        config: Dict[str, Any] = None
    ) -> BaseScraper:
        config = config or {}
        engine_type = engine_type.lower().strip()

        if engine_type == "shopify":
            return ShopifyScraper(base_url, source_id, config)

        elif engine_type == "bs4_woocommerce":
            return WooCommerceBs4Scraper(base_url, source_id, config)

        elif engine_type == "bs4_jumpseller":
            return JumpsellerBs4Scraper(base_url, source_id, config)

        elif engine_type == "playwright":
            return AuthPlaywrightScraper(base_url, source_id, config)

        else:
            raise ValueError(
                f"Unknown engine type: '{engine_type}'. "
                f"Valid types: shopify, bs4_woocommerce, bs4_jumpseller, playwright"
            )
