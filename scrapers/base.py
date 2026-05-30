from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class RawListing:
    """
    Canonical raw listing contract shared across all scraper strategies.
    Every scraper MUST populate at minimum: raw_title, price, url.
    All other fields are optional but should be populated when available.
    """
    raw_title: str
    price: float
    url: str

    # Brand/identity fields — extractable from Shopify's vendor field
    vendor: Optional[str] = None          # Direct brand name when available (Shopify)
    sku: Optional[str] = None             # Source SKU / external ID
    barcode: Optional[str] = None         # EAN-13 / UPC barcode

    # Rich metadata
    description: Optional[str] = None    # HTML or plain text description
    image_url: Optional[str] = None      # Primary product image URL
    tags: List[str] = field(default_factory=list)  # Category/type tags from source

    # Inventory
    stock: Optional[int] = None          # Stock count if available
    available: bool = True               # In-stock flag

    # Source context — populated by the worker
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

    async def extract_catalog(self) -> List[Dict[str, Any]]:
        """
        Extract the full product catalog from the source.
        Returns a list of dicts matching the RawListing contract.
        Subclasses MUST override this method.
        """
        raise NotImplementedError("Subclasses must implement extract_catalog()")