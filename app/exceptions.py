"""
Custom exception hierarchy for the Perfume Intelligence Platform.

Every exception has:
- message: human-readable error description
- context: dict of structured diagnostic data
- retryable: whether the operation can be retried
- to_dict(): serialization for API responses and logging
"""


# ══════════════════════════════════════════════════════════════════════════════
# ROOT
# ══════════════════════════════════════════════════════════════════════════════

class PerfumePlatformError(Exception):
    """Base exception for all platform errors."""

    def __init__(self, message: str, context: dict = None, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.context = context or {}
        self.retryable = retryable

    def to_dict(self) -> dict:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "retryable": self.retryable,
            "context": self.context,
        }


# ══════════════════════════════════════════════════════════════════════════════
# SCRAPER ERRORS
# ══════════════════════════════════════════════════════════════════════════════

class ScraperError(PerfumePlatformError):
    """Base class for all scraper-related errors."""
    pass


class NetworkError(ScraperError):
    """HTTP request failed (timeout, DNS, connection refused, non-2xx)."""

    def __init__(self, message: str, url: str = None, status_code: int = None):
        super().__init__(message, retryable=True)
        self.context = {"url": url, "status_code": status_code}


class AuthenticationError(ScraperError):
    """Login to a B2B portal failed."""

    def __init__(self, message: str, source_id: str = None):
        super().__init__(message, retryable=False)
        self.context = {"source_id": source_id}


class RateLimitError(ScraperError):
    """Source returned 429 Too Many Requests."""

    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message, retryable=True)
        self.context = {"retry_after_seconds": retry_after}


class ParseError(ScraperError):
    """HTML/JSON structure could not be parsed as expected."""

    def __init__(self, message: str, selector: str = None):
        super().__init__(message, retryable=False)
        self.context = {"selector": selector}


class CaptchaDetectedError(ScraperError):
    """CAPTCHA challenge detected on a B2B portal."""

    def __init__(self, message: str, source_id: str = None):
        super().__init__(message, retryable=False)
        self.context = {"source_id": source_id}


class SourceStructureChangedError(ScraperError):
    """Expected page structure no longer matches — site was redesigned."""

    def __init__(self, message: str, source_id: str = None, expected: str = None, found: str = None):
        super().__init__(message, retryable=False)
        self.context = {"source_id": source_id, "expected": expected, "found": found}


# ══════════════════════════════════════════════════════════════════════════════
# NORMALIZATION ERRORS
# ══════════════════════════════════════════════════════════════════════════════

class NormalizationError(PerfumePlatformError):
    """Base class for normalization pipeline errors."""
    pass


class RegexNormalizationError(NormalizationError):
    """Regex-based extraction failed for a product title."""
    pass


class LLMError(NormalizationError):
    """LLM API call failed (network, rate limit, internal error)."""

    def __init__(self, message: str, provider: str = None, model: str = None):
        super().__init__(message, retryable=True)
        self.context = {"provider": provider, "model": model}


class LLMInvalidResponseError(NormalizationError):
    """LLM returned a response that could not be parsed as valid JSON."""

    def __init__(self, message: str, raw_response: str = None):
        super().__init__(message, retryable=False)
        self.context = {"raw_response": raw_response[:500] if raw_response else None}


class LLMRefusalError(NormalizationError):
    """LLM refused to process the input (content policy, guardrails)."""
    pass


# ══════════════════════════════════════════════════════════════════════════════
# DATABASE ERRORS
# ══════════════════════════════════════════════════════════════════════════════

class DatabaseError(PerfumePlatformError):
    """Base class for database-related errors."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message, retryable=retryable)


class ConnectionError(DatabaseError):
    """Could not connect to the database."""
    pass


class IntegrityError(DatabaseError):
    """Constraint violation (unique, foreign key, check)."""

    def __init__(self, message: str, constraint: str = None):
        super().__init__(message, retryable=False)
        self.context = {"constraint": constraint}


class ConcurrentUpdateError(DatabaseError):
    """Optimistic locking conflict — row was modified by another transaction."""

    def __init__(self, message: str, table: str = None, row_id: str = None):
        super().__init__(message, retryable=True)
        self.context = {"table": table, "row_id": row_id}


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCT CODE ERRORS
# ══════════════════════════════════════════════════════════════════════════════

class ProductCodeError(PerfumePlatformError):
    """Base class for product code generation errors."""
    pass


class ProductCodeExhaustedError(ProductCodeError):
    """All codes in the current prefix range have been exhausted."""
    pass


# ══════════════════════════════════════════════════════════════════════════════
# BUSINESS LOGIC ERRORS
# ══════════════════════════════════════════════════════════════════════════════

class BusinessLogicError(PerfumePlatformError):
    """Base class for business rule violations."""
    pass


class InvalidStateTransitionError(BusinessLogicError):
    """Attempted an invalid availability state transition."""

    def __init__(self, message: str, from_state: str = None, to_state: str = None):
        super().__init__(message, retryable=False)
        self.context = {"from_state": from_state, "to_state": to_state}


class ProductNotFoundError(BusinessLogicError):
    """Product lookup returned no results."""

    def __init__(self, message: str, identifier: str = None):
        super().__init__(message, retryable=False)
        self.context = {"identifier": identifier}


class SourceNotFoundError(BusinessLogicError):
    """Source lookup returned no results."""

    def __init__(self, message: str, source_id: str = None):
        super().__init__(message, retryable=False)
        self.context = {"source_id": source_id}


# ══════════════════════════════════════════════════════════════════════════════
# INFRASTRUCTURE ERRORS
# ══════════════════════════════════════════════════════════════════════════════

class ConfigurationError(PerfumePlatformError):
    """Missing or invalid configuration (env vars, config files)."""
    pass


class StorageError(PerfumePlatformError):
    """Storage backend operation failed."""

    def __init__(self, message: str, backend: str = None):
        super().__init__(message, retryable=True)
        self.context = {"backend": backend}
