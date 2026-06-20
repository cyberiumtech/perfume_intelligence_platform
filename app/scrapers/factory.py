"""
Scraper factory — maps engine_type to concrete scraper strategy.
"""
from typing import Dict, Any

from .base import BaseScraper
from .shopify import ShopifyScraper
from .woocommerce import WooCommerceBs4Scraper
from .jumpseller import JumpsellerBs4Scraper
from .playwright_b2b import AuthPlaywrightScraper


class ScraperFactory:
    """
    Factory that maps Source.engine_type → concrete scraper strategy.

    Supported engine types:
      - "shopify"           → ShopifyScraper       (products.json API)
      - "bs4_woocommerce"   → WooCommerceBs4Scraper (WordPress/WooCommerce HTML)
      - "bs4_jumpseller"    → JumpsellerBs4Scraper  (Jumpseller SaaS HTML)
      - "playwright"        → AuthPlaywrightScraper  (B2B login + JS-rendered)
    """

    _REGISTRY: Dict[str, type] = {
        "shopify": ShopifyScraper,
        "bs4_woocommerce": WooCommerceBs4Scraper,
        "bs4_jumpseller": JumpsellerBs4Scraper,
        "playwright": AuthPlaywrightScraper,
    }

    @classmethod
    def get_scraper(
        cls,
        engine_type: str,
        base_url: str,
        source_id: str,
        config: Dict[str, Any] = None,
    ) -> BaseScraper:
        """
        Create and return the appropriate scraper for the given engine type.

        Args:
            engine_type: One of: shopify, bs4_woocommerce, bs4_jumpseller, playwright
            base_url: The source's base URL
            source_id: UUID string of the source
            config: Source-specific configuration dict

        Returns:
            Concrete BaseScraper subclass instance

        Raises:
            ValueError: If engine_type is not recognized
        """
        config = config or {}
        key = engine_type.lower().strip()

        scraper_class = cls._REGISTRY.get(key)
        if scraper_class is None:
            valid = ", ".join(sorted(cls._REGISTRY.keys()))
            raise ValueError(
                f"Unknown engine type: '{engine_type}'. Valid types: {valid}"
            )

        return scraper_class(base_url, source_id, config)

    @classmethod
    def register(cls, engine_type: str, scraper_class: type) -> None:
        """Register a custom scraper class for a new engine type."""
        cls._REGISTRY[engine_type.lower().strip()] = scraper_class
