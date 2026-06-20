"""
Sequential alphanumeric product code generator.

Format: [letter-prefix][zero-padded-number]
Examples: a00000001, a99999999, b00000001, z99999999, aa00000001

Capacity: (26 + 26^2 + 26^3 + ...) × 99,999,999 = effectively infinite.

Thread-safe via database SELECT FOR UPDATE SKIP LOCKED.
"""
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from .exceptions import ProductCodeError, ProductCodeExhaustedError

log = logging.getLogger(__name__)

# Regex pattern for valid product codes
PRODUCT_CODE_PATTERN = re.compile(r'^[a-z]+[0-9]{8}$')

# Maximum number per prefix (00000001 to 99999999)
MAX_NUMBER = 99_999_999
NUMBER_WIDTH = 8


@dataclass
class ParsedCode:
    """A product code split into its prefix and numeric parts."""
    prefix: str
    number: int
    raw: str


def parse_code(code: str) -> ParsedCode:
    """
    Validate and parse a product code string.

    Args:
        code: Product code like 'a00000001' or 'ab12345678'

    Returns:
        ParsedCode with prefix and number separated

    Raises:
        ProductCodeError: If the code format is invalid
    """
    if not code or not PRODUCT_CODE_PATTERN.match(code):
        raise ProductCodeError(
            f"Invalid product code format: '{code}'. Expected pattern: [a-z]+[0-9]{{{NUMBER_WIDTH}}}"
        )

    # Split at the boundary between letters and digits
    match = re.match(r'^([a-z]+)(\d{8})$', code)
    if not match:
        raise ProductCodeError(f"Failed to parse product code: '{code}'")

    prefix = match.group(1)
    number = int(match.group(2))

    return ParsedCode(prefix=prefix, number=number, raw=code)


def _prefix_to_number(prefix: str) -> int:
    """
    Convert a letter prefix to its base-26 numeric equivalent.

    'a' -> 0, 'b' -> 1, ..., 'z' -> 25,
    'aa' -> 26, 'ab' -> 27, ..., 'az' -> 51,
    'ba' -> 52, ...
    """
    result = 0
    for char in prefix:
        result = result * 26 + (ord(char) - ord('a'))
    # Adjust for variable-length: single chars are 0-25, double are 26+
    # 'a' = 0, 'z' = 25, 'aa' = 26, 'az' = 51, 'ba' = 52, etc.
    if len(prefix) == 1:
        return ord(prefix) - ord('a')

    # For multi-char: offset by sum of previous lengths
    offset = 0
    for length in range(1, len(prefix)):
        offset += 26 ** length

    value = 0
    for char in prefix:
        value = value * 26 + (ord(char) - ord('a'))

    return offset + value


def _number_to_prefix(n: int) -> str:
    """
    Convert a base-26 number back to a letter prefix.

    0 -> 'a', 25 -> 'z',
    26 -> 'aa', 51 -> 'az',
    52 -> 'ba', ...
    """
    if n < 0:
        raise ProductCodeError(f"Prefix number cannot be negative: {n}")

    # Determine the length tier
    # Length 1: 0-25 (26 prefixes)
    # Length 2: 26-701 (26^2 = 676 prefixes)
    # Length 3: 702-18277 (26^3 = 17576 prefixes)
    remaining = n
    length = 1
    tier_size = 26

    while remaining >= tier_size:
        remaining -= tier_size
        length += 1
        tier_size = 26 ** length

    # Convert remaining to base-26 digits of the correct length
    chars = []
    value = remaining
    for _ in range(length):
        chars.append(chr(ord('a') + (value % 26)))
        value //= 26
    chars.reverse()

    return ''.join(chars)


def _format_code(prefix: str, number: int) -> str:
    """Format a prefix and number into a product code string."""
    return f"{prefix}{number:0{NUMBER_WIDTH}d}"


def _next_code(prefix: str, number: int) -> Tuple[str, int]:
    """
    Compute the next (prefix, number) pair.

    If number < MAX_NUMBER, increment number.
    Otherwise, advance to next prefix and reset number to 1.
    """
    if number < MAX_NUMBER:
        return prefix, number + 1

    # Advance prefix
    prefix_num = _prefix_to_number(prefix)
    next_prefix = _number_to_prefix(prefix_num + 1)
    return next_prefix, 1


def get_next_code_from_db(db: Session) -> str:
    """
    Query the database for the highest existing product code and return the next one.

    Uses SELECT FOR UPDATE SKIP LOCKED for thread safety in concurrent environments.

    Args:
        db: SQLAlchemy session

    Returns:
        Next product code string (e.g., 'a00000002')

    Raises:
        ProductCodeError: If code generation fails
        ProductCodeExhaustedError: If all codes are exhausted (practically impossible)
    """
    try:
        # Find the highest product_code using lexicographic ordering
        # This works because codes are fixed-width within each prefix tier
        result = db.execute(
            text("""
                SELECT product_code
                FROM products
                ORDER BY LENGTH(product_code) DESC, product_code DESC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """)
        ).fetchone()

        if result is None or result[0] is None:
            # No products exist yet — start with a00000001
            return _format_code('a', 1)

        current_code = result[0]
        parsed = parse_code(current_code)
        next_prefix, next_number = _next_code(parsed.prefix, parsed.number)

        return _format_code(next_prefix, next_number)

    except (ProductCodeError, ProductCodeExhaustedError):
        raise
    except Exception as e:
        log.error(f"Product code generation failed: {e}", exc_info=True)
        raise ProductCodeError(f"Failed to generate next product code: {e}")


def generate_emergency_code() -> str:
    """
    Generate a fallback product code using hash + timestamp.

    Used when the sequential generator fails (e.g., database lock contention).
    Format still matches the pattern: [a-z]+[0-9]{8}

    Returns:
        Emergency product code like 'emrg12345678'
    """
    timestamp = str(time.time()).encode('utf-8')
    hash_hex = hashlib.sha256(timestamp).hexdigest()

    # Convert first 8 hex chars to decimal, take last 8 digits
    numeric_part = int(hash_hex[:10], 16) % MAX_NUMBER
    if numeric_part == 0:
        numeric_part = 1

    code = f"emrg{numeric_part:0{NUMBER_WIDTH}d}"

    if not PRODUCT_CODE_PATTERN.match(code):
        # Absolute fallback
        code = f"x{int(time.time()) % MAX_NUMBER:0{NUMBER_WIDTH}d}"

    log.warning(f"Generated emergency product code: {code}")
    return code
