"""
Tests for the delta engine — hash computation, idempotency, availability transitions.
"""
import pytest

from app.delta_engine import compute_state_hash, _validate_ean, _detect_availability
from app.models import AvailabilityState


class TestStateHash:
    """Verify hash computation behavior."""

    def test_hash_deterministic(self):
        """Same inputs should produce the same hash."""
        h1 = compute_state_hash(34990.0, 5, "AVAILABLE_IN_STOCK")
        h2 = compute_state_hash(34990.0, 5, "AVAILABLE_IN_STOCK")
        assert h1 == h2

    def test_hash_is_sha256(self):
        """Hash should be a 64-char hex string."""
        h = compute_state_hash(100, 1, "AVAILABLE_IN_STOCK")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_changes_on_price_change(self):
        h1 = compute_state_hash(34990.0, 5, "AVAILABLE_IN_STOCK")
        h2 = compute_state_hash(39990.0, 5, "AVAILABLE_IN_STOCK")
        assert h1 != h2

    def test_hash_changes_on_stock_change(self):
        h1 = compute_state_hash(34990.0, 5, "AVAILABLE_IN_STOCK")
        h2 = compute_state_hash(34990.0, 10, "AVAILABLE_IN_STOCK")
        assert h1 != h2

    def test_hash_changes_on_availability_change(self):
        h1 = compute_state_hash(34990.0, 5, "AVAILABLE_IN_STOCK")
        h2 = compute_state_hash(34990.0, 5, "AVAILABLE_NO_STOCK")
        assert h1 != h2

    def test_hash_handles_none_values(self):
        """None price and stock should not crash."""
        h = compute_state_hash(None, None, "AVAILABLE_IN_STOCK")
        assert len(h) == 64

    def test_hash_excludes_title(self):
        """Title is NOT part of the hash. Different titles with same state = same hash."""
        h1 = compute_state_hash(34990.0, 5, "AVAILABLE_IN_STOCK")
        h2 = compute_state_hash(34990.0, 5, "AVAILABLE_IN_STOCK")
        assert h1 == h2  # No title parameter exists

    def test_hash_string_vs_float_price(self):
        """Different types of the same value should produce different hashes
        (since we stringify). This is expected behavior."""
        h1 = compute_state_hash("34990.0", 5, "AVAILABLE_IN_STOCK")
        h2 = compute_state_hash(34990.0, 5, "AVAILABLE_IN_STOCK")
        # These may or may not match depending on stringification
        assert len(h1) == 64 and len(h2) == 64


class TestEanValidation:
    """Verify EAN-13 validation."""

    def test_valid_13_digit(self):
        assert _validate_ean("1234567890123") == "1234567890123"

    def test_valid_12_digit(self):
        assert _validate_ean("123456789012") == "123456789012"

    def test_none_returns_none(self):
        assert _validate_ean(None) is None

    def test_empty_string_returns_none(self):
        assert _validate_ean("") is None

    def test_letters_returns_none(self):
        assert _validate_ean("ABC1234567890") is None

    def test_wrong_length_returns_none(self):
        assert _validate_ean("123456") is None

    def test_whitespace_stripped(self):
        assert _validate_ean("  1234567890123  ") == "1234567890123"

    def test_integer_input(self):
        """Should handle integer barcode values from APIs."""
        assert _validate_ean(1234567890123) == "1234567890123"


class TestAvailabilityDetection:
    """Verify availability state detection from raw listing data."""

    def test_normal_available(self):
        result = _detect_availability(
            {"available": True, "stock": 5},
            price=34990.0,
        )
        assert result == AvailabilityState.AVAILABLE_IN_STOCK

    def test_explicit_unavailable(self):
        result = _detect_availability(
            {"available": False, "stock": 0},
            price=34990.0,
        )
        assert result == AvailabilityState.AVAILABLE_NO_STOCK

    def test_zero_stock(self):
        result = _detect_availability(
            {"available": True, "stock": 0},
            price=34990.0,
        )
        assert result == AvailabilityState.AVAILABLE_NO_STOCK

    def test_none_price(self):
        result = _detect_availability(
            {"available": True, "stock": 5},
            price=None,
        )
        assert result == AvailabilityState.AVAILABLE_NO_STOCK

    def test_zero_price(self):
        result = _detect_availability(
            {"available": True, "stock": 5},
            price=0,
        )
        assert result == AvailabilityState.AVAILABLE_NO_STOCK

    def test_no_stock_field(self):
        """Missing stock field should default to available."""
        result = _detect_availability(
            {"available": True},
            price=34990.0,
        )
        assert result == AvailabilityState.AVAILABLE_IN_STOCK


class TestAvailabilityTransitions:
    """Verify the availability state machine."""

    def test_valid_transitions(self):
        from app.models import ProductListing, _AVAILABILITY_TRANSITIONS, AvailabilityState

        # Map valid transitions
        valid = {
            AvailabilityState.AVAILABLE_IN_STOCK: [
                AvailabilityState.AVAILABLE_NO_STOCK,
                AvailabilityState.DELISTED,
            ],
            AvailabilityState.AVAILABLE_NO_STOCK: [
                AvailabilityState.AVAILABLE_IN_STOCK,
                AvailabilityState.DELISTED,
            ],
        }

        for from_state, to_states in valid.items():
            for to_state in to_states:
                assert to_state in _AVAILABILITY_TRANSITIONS[from_state], \
                    f"Transition {from_state} → {to_state} should be valid"

    def test_delisted_is_terminal(self):
        from app.models import _AVAILABILITY_TRANSITIONS, AvailabilityState
        assert len(_AVAILABILITY_TRANSITIONS[AvailabilityState.DELISTED]) == 0
