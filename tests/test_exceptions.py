"""
Tests for the exception hierarchy.
"""
import pytest

from app.exceptions import (
    PerfumePlatformError,
    ScraperError, NetworkError, AuthenticationError, RateLimitError,
    ParseError, CaptchaDetectedError, SourceStructureChangedError,
    NormalizationError, RegexNormalizationError, LLMError,
    LLMInvalidResponseError, LLMRefusalError,
    DatabaseError, ConnectionError, IntegrityError, ConcurrentUpdateError,
    ProductCodeError, ProductCodeExhaustedError,
    BusinessLogicError, InvalidStateTransitionError,
    ProductNotFoundError, SourceNotFoundError,
    ConfigurationError, StorageError,
)


class TestExceptionHierarchy:
    """Verify the inheritance chain is correct."""

    def test_root_inherits_from_exception(self):
        assert issubclass(PerfumePlatformError, Exception)

    def test_scraper_errors_inherit_from_root(self):
        for exc_class in [
            ScraperError, NetworkError, AuthenticationError,
            RateLimitError, ParseError, CaptchaDetectedError,
            SourceStructureChangedError,
        ]:
            assert issubclass(exc_class, PerfumePlatformError), f"{exc_class.__name__} is not a PerfumePlatformError"
            assert issubclass(exc_class, ScraperError), f"{exc_class.__name__} is not a ScraperError"

    def test_normalization_errors_inherit_from_root(self):
        for exc_class in [
            NormalizationError, RegexNormalizationError,
            LLMError, LLMInvalidResponseError, LLMRefusalError,
        ]:
            assert issubclass(exc_class, PerfumePlatformError)
            assert issubclass(exc_class, NormalizationError)

    def test_database_errors_inherit_from_root(self):
        for exc_class in [DatabaseError, ConnectionError, IntegrityError, ConcurrentUpdateError]:
            assert issubclass(exc_class, PerfumePlatformError)
            assert issubclass(exc_class, DatabaseError)

    def test_business_errors_inherit_from_root(self):
        for exc_class in [
            BusinessLogicError, InvalidStateTransitionError,
            ProductNotFoundError, SourceNotFoundError,
        ]:
            assert issubclass(exc_class, PerfumePlatformError)
            assert issubclass(exc_class, BusinessLogicError)

    def test_product_code_errors_inherit_from_root(self):
        assert issubclass(ProductCodeError, PerfumePlatformError)
        assert issubclass(ProductCodeExhaustedError, ProductCodeError)


class TestExceptionSerialization:
    """Verify to_dict() works correctly."""

    def test_to_dict_basic(self):
        exc = PerfumePlatformError("test error")
        d = exc.to_dict()
        assert d["error_type"] == "PerfumePlatformError"
        assert d["message"] == "test error"
        assert d["retryable"] is False
        assert d["context"] == {}

    def test_network_error_context(self):
        exc = NetworkError("timeout", url="https://example.com", status_code=503)
        d = exc.to_dict()
        assert d["retryable"] is True
        assert d["context"]["url"] == "https://example.com"
        assert d["context"]["status_code"] == 503

    def test_rate_limit_error_context(self):
        exc = RateLimitError("slow down", retry_after=120)
        assert exc.retryable is True
        assert exc.context["retry_after_seconds"] == 120

    def test_llm_invalid_response_truncation(self):
        long_response = "x" * 1000
        exc = LLMInvalidResponseError("bad json", raw_response=long_response)
        assert len(exc.context["raw_response"]) == 500

    def test_integrity_error_not_retryable(self):
        exc = IntegrityError("unique violation", constraint="uq_product_identity")
        assert exc.retryable is False
        assert exc.context["constraint"] == "uq_product_identity"

    def test_concurrent_update_retryable(self):
        exc = ConcurrentUpdateError("conflict", table="products", row_id="abc-123")
        assert exc.retryable is True

    def test_state_transition_error_context(self):
        exc = InvalidStateTransitionError(
            "invalid", from_state="DELISTED", to_state="AVAILABLE_IN_STOCK"
        )
        assert exc.context["from_state"] == "DELISTED"
        assert exc.context["to_state"] == "AVAILABLE_IN_STOCK"
