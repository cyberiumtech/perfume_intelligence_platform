"""
Base scraper classes and data structures.

All scrapers inherit from BaseScraper and return List[Dict] matching RawListing.
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

import httpx

from ..exceptions import NetworkError, RateLimitError, ParseError

log = logging.getLogger(__name__)


@dataclass
class RawListing:
    """
    Canonical raw listing contract shared across all scraper strategies.
    Every scraper MUST populate at minimum: raw_title, price, url.
    """
    raw_title: str
    price: float
    url: str

    # Brand/identity fields
    vendor: Optional[str] = None
    sku: Optional[str] = None
    barcode: Optional[str] = None

    # Rich metadata
    description: Optional[str] = None
    image_url: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    # Inventory
    stock: Optional[int] = None
    available: bool = True

    # Source context
    source_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_title": self.raw_title,
            "price": self.price,
            "url": self.url,
            "vendor": self.vendor,
            "sku": self.sku,
            "barcode": self.barcode,
            "description": self.description,
            "image_url": self.image_url,
            "tags": self.tags,
            "stock": self.stock,
            "available": self.available,
        }


class BaseScraper:
    """
    Abstract base class for all scraper strategies.
    Subclasses implement extract_catalog() to return a list of RawListing dicts.
    """

    def __init__(self, base_url: str, source_id: str, config: Dict[str, Any] = None):
        self.base_url = base_url.rstrip("/")
        self.source_id = source_id
        self.config = config or {}
        self.rate_limit_delay = float(self.config.get("rate_limit_delay", 1.0))
        self._last_request_time = 0.0

    async def extract_catalog(self) -> List[Dict[str, Any]]:
        """
        Extract the full product catalog from the source.
        Returns a list of dicts matching the RawListing contract.
        """
        raise NotImplementedError("Subclasses must implement extract_catalog()")

    async def _rate_limited_request(
        self,
        client: httpx.AsyncClient,
        url: str,
        method: str = "GET",
        **kwargs,
    ) -> httpx.Response:
        """
        Execute an HTTP request with rate limiting.

        Enforces a minimum delay between requests to the same source.
        Handles HTTP error responses with appropriate exceptions.
        """
        # Rate limiting
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.rate_limit_delay:
            await asyncio.sleep(self.rate_limit_delay - elapsed)

        self._last_request_time = time.time()

        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as e:
            raise NetworkError(f"Request timeout: {e}", url=url)
        except httpx.RequestError as e:
            raise NetworkError(f"Network error: {e}", url=url)

        # Handle error responses
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            raise RateLimitError(
                f"Rate limited by {url}",
                retry_after=retry_after,
            )

        if response.status_code >= 500:
            raise NetworkError(
                f"Server error {response.status_code}",
                url=url,
                status_code=response.status_code,
            )

        return response

    def _handle_network_error(self, error: Exception, context: str = "") -> None:
        """Log and handle network errors without crashing the batch."""
        log.error(f"[{self.__class__.__name__}] {context}: {error}")

    @staticmethod
    def _validate_ean(barcode: Any) -> Optional[str]:
        """
        Validate EAN-13/UPC barcode: digits only, 12-13 chars.
        Returns cleaned barcode or None.
        """
        if not barcode:
            return None
        clean = str(barcode).strip()
        if not clean.isdigit():
            return None
        if len(clean) not in (12, 13):
            return None
        return clean

    @staticmethod
    def _detect_out_of_stock(text: str) -> bool:
        """
        Detect out-of-stock indicators in text.
        Checks for Spanish and English markers.
        """
        if not text:
            return False
        lower = text.lower()
        markers = [
            "agotado", "sin stock", "out of stock", "no disponible",
            "sold out", "unavailable", "no stock", "stock: 0",
        ]
        return any(m in lower for m in markers)

    @staticmethod
    def _parse_price(text: str) -> float:
        """Parse Chilean price strings like '$34.990' → 34990.0"""
        digits = re.sub(r"[^\d]", "", text or "")
        return float(digits) if digits else 0.0

    @staticmethod
    def _clean_text(text: Optional[str]) -> str:
        """Strip and normalize whitespace."""
        if not text:
            return ""
        return re.sub(r"\s+", " ", text.strip())

    @staticmethod
    def _strip_html(html: str) -> str:
        """Remove HTML tags and collapse whitespace."""
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _get_headers(self) -> Dict[str, str]:
        """Default HTTP headers for scraping."""
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
        }
