"""
Tests for the product code generator.
"""
import pytest

from app.product_code_generator import (
    parse_code, _prefix_to_number, _number_to_prefix,
    _format_code, _next_code, generate_emergency_code,
    PRODUCT_CODE_PATTERN,
)
from app.exceptions import ProductCodeError


class TestProductCodeFormat:
    """Verify the product code format regex."""

    @pytest.mark.parametrize("code", [
        "a00000001",
        "a99999999",
        "b00000001",
        "z99999999",
        "aa00000001",
        "ab12345678",
        "zz99999999",
        "emrg12345678",
    ])
    def test_valid_codes(self, code):
        assert PRODUCT_CODE_PATTERN.match(code) is not None

    @pytest.mark.parametrize("code", [
        "",
        "A00000001",    # uppercase
        "a0000001",     # 7 digits
        "a000000001",   # 9 digits
        "100000001",    # starts with digit
        "a-00000001",   # hyphen
        "a 00000001",   # space
    ])
    def test_invalid_codes(self, code):
        assert PRODUCT_CODE_PATTERN.match(code) is None


class TestParseCode:
    """Verify code parsing."""

    def test_parse_simple_code(self):
        parsed = parse_code("a00000001")
        assert parsed.prefix == "a"
        assert parsed.number == 1
        assert parsed.raw == "a00000001"

    def test_parse_max_single_prefix(self):
        parsed = parse_code("z99999999")
        assert parsed.prefix == "z"
        assert parsed.number == 99999999

    def test_parse_double_prefix(self):
        parsed = parse_code("aa00000001")
        assert parsed.prefix == "aa"
        assert parsed.number == 1

    def test_parse_emergency_code(self):
        parsed = parse_code("emrg12345678")
        assert parsed.prefix == "emrg"
        assert parsed.number == 12345678

    def test_parse_invalid_raises(self):
        with pytest.raises(ProductCodeError):
            parse_code("INVALID")

    def test_parse_empty_raises(self):
        with pytest.raises(ProductCodeError):
            parse_code("")

    def test_parse_none_raises(self):
        with pytest.raises(ProductCodeError):
            parse_code(None)


class TestPrefixConversion:
    """Verify base-26 prefix conversion."""

    def test_single_chars(self):
        assert _prefix_to_number("a") == 0
        assert _prefix_to_number("b") == 1
        assert _prefix_to_number("z") == 25

    def test_double_chars(self):
        assert _prefix_to_number("aa") == 26
        assert _prefix_to_number("ab") == 27
        assert _prefix_to_number("az") == 51
        assert _prefix_to_number("ba") == 52

    def test_roundtrip(self):
        """Verify prefix → number → prefix roundtrip."""
        for i in range(100):
            prefix = _number_to_prefix(i)
            assert _prefix_to_number(prefix) == i

    def test_number_to_prefix_basic(self):
        assert _number_to_prefix(0) == "a"
        assert _number_to_prefix(25) == "z"
        assert _number_to_prefix(26) == "aa"
        assert _number_to_prefix(51) == "az"
        assert _number_to_prefix(52) == "ba"


class TestNextCode:
    """Verify sequential code generation."""

    def test_increment_number(self):
        prefix, number = _next_code("a", 1)
        assert prefix == "a"
        assert number == 2

    def test_rollover_prefix(self):
        prefix, number = _next_code("a", 99999999)
        assert prefix == "b"
        assert number == 1

    def test_rollover_z_to_aa(self):
        prefix, number = _next_code("z", 99999999)
        assert prefix == "aa"
        assert number == 1

    def test_format_code(self):
        assert _format_code("a", 1) == "a00000001"
        assert _format_code("a", 99999999) == "a99999999"
        assert _format_code("aa", 42) == "aa00000042"


class TestEmergencyCode:
    """Verify emergency fallback code generation."""

    def test_emergency_code_format(self):
        code = generate_emergency_code()
        assert PRODUCT_CODE_PATTERN.match(code) is not None

    def test_emergency_codes_unique(self):
        import time
        codes = set()
        for _ in range(10):
            codes.add(generate_emergency_code())
            time.sleep(0.01)
        # Most should be unique (timing-based)
        assert len(codes) >= 5
