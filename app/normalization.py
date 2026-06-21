"""
Normalization pipeline for the Perfume Intelligence Platform.

4 strategies, only HybridNormalizer is kept for production:
  A. RegexNormalizer      — Known brand library + regex extraction
  B. BedrockNormalizer    — AWS Bedrock Claude 3 Haiku
  C. OllamaNormalizer     — Local Ollama llama3.2
  D. HybridNormalizer     — Regex first, LLM only for gaps (PRODUCTION DEFAULT)

Target: 15-25% LLM call rate (vs 100% in the old code).
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import Optional, Dict, Any, List, Tuple

from .exceptions import (
    NormalizationError, RegexNormalizationError,
    LLMError, LLMInvalidResponseError, LLMRefusalError,
)

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class NormalizedProduct:
    """Output of the normalization pipeline."""
    brand: str
    product_name: str
    variant: Optional[str] = None
    fragrance_type: Optional[str] = None  # EDP, EDT, PARFUM, COLOGNE, BODY_MIST
    volume_ml: Optional[int] = None
    gender: Optional[str] = None  # M, F, UNISEX
    ean_13: Optional[str] = None
    normalization_method: str = "REGEX"
    confidence_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
# KNOWN BRAND LIBRARY — Chilean perfume market
# ══════════════════════════════════════════════════════════════════════════════

# Multi-word brands must come before single-word to avoid partial matches
KNOWN_BRANDS: List[str] = [
    # 4-word brands
    "Jean Paul Gaultier",
    # 3-word brands
    "Yves Saint Laurent",
    "Carolina Herrera",
    "Dolce & Gabbana",
    "Dolce and Gabbana",
    "Dolce Gabbana",
    "Van Cleef & Arpels",
    "Van Cleef Arpels",
    "Narciso Rodriguez",
    "Issey Miyake",
    "Viktor & Rolf",
    "Viktor Rolf",
    "Abercrombie & Fitch",
    "Abercrombie Fitch",
    "Oscar De La Renta",
    "Elizabeth Arden",
    # 2-word brands
    "Hugo Boss",
    "Calvin Klein",
    "Tom Ford",
    "Ralph Lauren",
    "Marc Jacobs",
    "Paco Rabanne",
    "Giorgio Armani",
    "Ermenegildo Zegna",
    "Salvatore Ferragamo",
    "Roberto Cavalli",
    "Bvlgari Bulgari",
    "Antonio Banderas",
    "Huda Beauty",
    "Jo Malone",
    "Maison Margiela",
    "Agent Provocateur",
    "Juliette Has A Gun",
    # 1-word brands
    "Dior",
    "Chanel",
    "Armani",
    "Versace",
    "Gucci",
    "Prada",
    "Burberry",
    "Givenchy",
    "Hermès",
    "Hermes",
    "Cartier",
    "Bvlgari",
    "Bulgari",
    "Lancome",
    "Lancôme",
    "Lacoste",
    "Davidoff",
    "Montblanc",
    "Mont Blanc",
    "Kenzo",
    "Azzaro",
    "Ferragamo",
    "Loewe",
    "Coach",
    "Valentino",
    "Moschino",
    "Miu Miu",
    "Chloe",
    "Chloé",
    "Thierry Mugler",
    "Mugler",
    "Balenciaga",
    "Diesel",
    "Guess",
    "Benetton",
    "Nautica",
    "Clinique",
    "Estée Lauder",
    "Estee Lauder",
    "Michael Kors",
    "Vera Wang",
    "Tommy Hilfiger",
    "Jaguar",
    "Ferrari",
    "Bentley",
    "Dunhill",
    "Mancera",
    "Lattafa",
    "Rasasi",
    "Ajmal",
    "Afnan",
    "Nishane",
    "Xerjoff",
    "Byredo",
    "Creed",
    "Amouage",
    "Penhaligon",
    "Acqua di Parma",
    "Maison Francis Kurkdjian",
    "Tiffany",
    "Jimmy Choo",
    "Elie Saab",
    "Nina Ricci",
    "Rochas",
    "Lolita Lempicka",
    "Shakira",
    "Benetton",
    "Replay",
    "Police",
    "Hollister",
    "Banana Republic",
]

# Build a fast lookup: lowercase brand → original casing
_BRAND_LOOKUP: Dict[str, str] = {}
for _b in KNOWN_BRANDS:
    _BRAND_LOOKUP[_b.lower()] = _b
    # Also add without accents
    _no_accents = _b.lower().replace("é", "e").replace("ô", "o").replace("ë", "e").replace("è", "e")
    if _no_accents != _b.lower():
        _BRAND_LOOKUP[_no_accents] = _b

# Sort by length descending so longer brands match first
_SORTED_BRAND_KEYS = sorted(_BRAND_LOOKUP.keys(), key=len, reverse=True)


# ══════════════════════════════════════════════════════════════════════════════
# REGEX PATTERNS
# ══════════════════════════════════════════════════════════════════════════════

_ML_PATTERN = re.compile(
    r'\b(\d{1,5})\s*(?:ml|ML|Ml|mL)\b',
    re.IGNORECASE,
)

# Use word boundaries to avoid matching inside words (e.g., "seduction" doesn't match "edt")
_FRAGRANCE_PATTERNS: Dict[str, re.Pattern] = {
    "PARFUM": re.compile(r'\b(?:extrait\s+de\s+parfum|pure\s+parfum|parfum\s+extrait)\b', re.IGNORECASE),
    "EDP": re.compile(r'\b(?:edp|eau\s+de\s+parfum)\b', re.IGNORECASE),
    "EDT": re.compile(r'\b(?:edt|eau\s+de\s+toilette)\b', re.IGNORECASE),
    "COLOGNE": re.compile(r'\b(?:edc|eau\s+de\s+cologne|cologne)\b', re.IGNORECASE),
    "BODY_MIST": re.compile(r'\b(?:body\s+mist|body\s+splash|splash|mist)\b', re.IGNORECASE),
}

_GENDER_PATTERNS: Dict[str, re.Pattern] = {
    "M": re.compile(r'\b(?:hombre|men|man|masculino|masculine|him|pour\s+homme)\b', re.IGNORECASE),
    "F": re.compile(r'\b(?:mujer|women|woman|femenino|feminine|her|pour\s+femme|dama)\b', re.IGNORECASE),
    "UNISEX": re.compile(r'\b(?:unisex|unisexo)\b', re.IGNORECASE),
}


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY A: REGEX NORMALIZER
# ══════════════════════════════════════════════════════════════════════════════

class RegexNormalizer:
    """
    Extract product fields from title using regex patterns and known brand library.

    Confidence = fraction of critical fields resolved (brand, product_name, fragrance_type, volume_ml).
    """

    def normalize(self, raw_title: str, vendor: str = None, tags: List[str] = None,
                  barcode: str = None) -> NormalizedProduct:
        """
        Normalize a raw product title using regex extraction.

        Args:
            raw_title: The raw product listing title
            vendor: Brand from source metadata (e.g., Shopify vendor field)
            tags: Category/type tags from source
            barcode: EAN-13/UPC barcode

        Returns:
            NormalizedProduct with confidence score
        """
        tags = tags or []
        combined_text = f"{raw_title} {' '.join(tags)}".strip()

        # Extract fields
        brand = self._extract_brand(raw_title, vendor)
        volume_ml = self._extract_ml(raw_title)
        fragrance_type = self._extract_fragrance_type(combined_text)
        gender = self._extract_gender(combined_text)
        ean_13 = self._validate_ean(barcode)
        product_name = self._extract_product_name(raw_title, brand, volume_ml, fragrance_type, gender)
        variant = self._extract_variant(raw_title, brand, product_name)

        # Compute confidence
        critical_fields = [brand != "Unknown", bool(product_name), bool(fragrance_type), bool(volume_ml)]
        confidence = sum(critical_fields) / len(critical_fields)

        return NormalizedProduct(
            brand=brand,
            product_name=product_name or raw_title[:100],
            variant=variant,
            fragrance_type=fragrance_type,
            volume_ml=volume_ml,
            gender=gender,
            ean_13=ean_13,
            normalization_method="REGEX",
            confidence_score=round(confidence, 2),
        )

    def _extract_brand(self, title: str, vendor: str = None) -> str:
        """Extract brand from vendor field or title using known brand library."""
        # Vendor field is authoritative if available
        if vendor and vendor.strip():
            clean_vendor = vendor.strip()
            # Check if vendor matches a known brand
            vendor_lower = clean_vendor.lower()
            for brand_key in _SORTED_BRAND_KEYS:
                if vendor_lower == brand_key or vendor_lower.startswith(brand_key):
                    return _BRAND_LOOKUP[brand_key]
            return clean_vendor

        # Search title for known brands
        title_lower = title.lower()
        for brand_key in _SORTED_BRAND_KEYS:
            # Use word boundary matching for brand detection
            pattern = re.compile(r'\b' + re.escape(brand_key) + r'\b', re.IGNORECASE)
            if pattern.search(title_lower):
                return _BRAND_LOOKUP[brand_key]

        return "Unknown"

    def _extract_ml(self, text: str) -> Optional[int]:
        """Extract milliliter volume from text."""
        match = _ML_PATTERN.search(text)
        if match:
            ml = int(match.group(1))
            if 1 <= ml <= 10000:  # Sanity check
                return ml
        return None

    def _extract_fragrance_type(self, text: str) -> Optional[str]:
        """Extract fragrance type using word-boundary patterns."""
        # Check PARFUM first (longest match), then EDP, EDT, etc.
        for ftype, pattern in _FRAGRANCE_PATTERNS.items():
            if pattern.search(text):
                return ftype
        return None

    def _extract_gender(self, text: str) -> Optional[str]:
        """Infer gender from text using word-boundary patterns."""
        for gender, pattern in _GENDER_PATTERNS.items():
            if pattern.search(text):
                return gender
        return None

    def _validate_ean(self, barcode: str) -> Optional[str]:
        """Validate EAN-13/UPC barcode: digits only, 12-13 chars."""
        if not barcode:
            return None
        clean = barcode.strip()
        if not clean.isdigit():
            log.warning(f"Invalid EAN-13 (non-digit chars): '{barcode}'")
            return None
        if len(clean) not in (12, 13):
            log.warning(f"Invalid EAN-13 (wrong length {len(clean)}): '{barcode}'")
            return None
        return clean

    def _extract_product_name(self, title: str, brand: str, volume_ml: Optional[int],
                               fragrance_type: Optional[str], gender: Optional[str]) -> Optional[str]:
        """Strip brand, size, type, gender to get clean product name."""
        name = title

        # Remove brand prefix
        if brand and brand != "Unknown":
            name = re.sub(rf'^{re.escape(brand)}\s*[-–]?\s*', '', name, flags=re.IGNORECASE)

        # Remove ml
        name = _ML_PATTERN.sub('', name)

        # Remove fragrance type keywords
        for pattern in _FRAGRANCE_PATTERNS.values():
            name = pattern.sub('', name)

        # Remove gender keywords
        for pattern in _GENDER_PATTERNS.values():
            name = pattern.sub('', name)

        # Clean up punctuation and whitespace
        name = re.sub(r'[\(\)\[\]\-–_]+', ' ', name)
        name = re.sub(r'\s+', ' ', name).strip().strip('-').strip()

        return name if len(name) >= 2 else None

    def _extract_variant(self, title: str, brand: str, product_name: Optional[str]) -> Optional[str]:
        """Extract variant/edition from what remains after brand and product name are removed."""
        if not product_name:
            return None

        # Common variant keywords
        variant_patterns = [
            r'\b(Intense|Intenso|Intensément)\b',
            r'\b(Sport|Sporting)\b',
            r'\b(Night|Nuit)\b',
            r'\b(Absolu|Absolute)\b',
            r'\b(Extreme|Extrême)\b',
            r'\b(Légère|Legere|Light)\b',
            r'\b(Fresh|Fraîche)\b',
            r'\b(Limited Edition)\b',
            r'\b(Collector)\b',
            r'\b(Privé|Prive)\b',
            r'\b(Noir)\b',
            r'\b(Blanc|Blanche)\b',
            r'\b(Red|Blue|Black|White|Gold|Silver|Rose|Pink)\b',
            r'\b(Flame|Fire)\b',
        ]

        # Search in the product name part for variant suffixes
        for vp in variant_patterns:
            match = re.search(vp, product_name, re.IGNORECASE)
            if match:
                return match.group(1)

        return None


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY B: GEMINI NORMALIZER (Google AI Studio)
# ══════════════════════════════════════════════════════════════════════════════

class GeminiNormalizer:
    """
    Google Gemini (gemini-1.5-flash) via native HTTP requests.
    2-attempt retry.
    Injects regex-extracted values as pre-confirmed hints.
    """

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")

    def normalize(self, raw_title: str, hints: Dict[str, Any] = None,
                  description: str = "") -> NormalizedProduct:
        """Normalize using Gemini via google-genai SDK."""
        from google import genai
        from google.genai import types

        if not self.api_key:
            raise LLMError("GEMINI_API_KEY not found in environment variables", provider="gemini")

        hints = hints or {}
        prompt = self._build_prompt(raw_title, description, hints)

        for attempt in range(2):
            try:
                client = genai.Client(api_key=self.api_key)
                
                response = client.models.generate_content(
                    model='gemini-1.5-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.0,
                        max_output_tokens=512,
                    ),
                )
                
                extracted_text = response.text
                parsed = self._parse_response(extracted_text)

                # Merge: hints override LLM output
                for key in ["brand", "volume_ml", "fragrance_type", "gender"]:
                    hint_key = key if key != "volume_ml" else "ml"
                    if hints.get(hint_key) is not None:
                        if key == "volume_ml":
                            parsed["ml"] = hints["ml"]
                        else:
                            parsed[key] = hints[hint_key]

                return NormalizedProduct(
                    brand=parsed.get("brand", "Unknown"),
                    product_name=parsed.get("product_name", raw_title[:100]),
                    variant=parsed.get("variant"),
                    fragrance_type=parsed.get("fragrance_type"),
                    volume_ml=parsed.get("ml"),
                    gender=parsed.get("gender"),
                    normalization_method="LLM_GEMINI",
                    confidence_score=0.90,
                )

            except json.JSONDecodeError as e:
                log.warning(f"Gemini JSON parse error (attempt {attempt + 1}): {e}")
                prompt += "\n\nCRITICAL: Your last response was invalid JSON. You MUST output ONLY a valid JSON object."
            except LLMError:
                raise
            except Exception as e:
                log.error(f"Gemini error (attempt {attempt + 1}): {e}")
                if attempt == 1:
                    raise LLMError(f"Gemini failed after 2 attempts: {e}", provider="gemini",
                                   model="gemini-1.5-flash")

        raise LLMError(f"Gemini failed to return valid JSON after 2 attempts", provider="gemini")

    def _build_prompt(self, raw_title: str, description: str, hints: Dict[str, Any]) -> str:
        hint_lines = []
        if hints.get("brand"):
            hint_lines.append(f'- Brand is CONFIRMED as: "{hints["brand"]}" — do NOT change this')
        if hints.get("ml"):
            hint_lines.append(f'- Volume is CONFIRMED as: {hints["ml"]} ml — do NOT change this')
        if hints.get("fragrance_type"):
            hint_lines.append(f'- Fragrance type is CONFIRMED as: "{hints["fragrance_type"]}" — do NOT change this')
        if hints.get("gender"):
            hint_lines.append(f'- Gender is CONFIRMED as: "{hints["gender"]}" — do NOT change this')

        hints_block = "\n".join(hint_lines) if hint_lines else "No pre-confirmed values."

        return f"""You are a perfume data normalization specialist for a Chilean wholesale distributor database.

Analyze the following perfume product listing and extract the fields below.

Product Title: "{raw_title}"
Description: "{description[:300]}"

Pre-confirmed values (DO NOT modify these):
{hints_block}

Extract the following JSON fields:
- brand: The perfume manufacturer/house (e.g., "Dior", "Carolina Herrera")
- product_name: The specific fragrance name only, NO brand, NO size, NO type
- variant: Specific edition/variation if any (e.g., "Intense", "Sport") or null
- fragrance_type: ONE of: EDP, EDT, PARFUM, COLOGNE, BODY_MIST — or null
- ml: Integer volume in milliliters or null
- gender: ONE of: M, F, UNISEX

Output ONLY a single valid JSON object. No markdown, no explanation."""

    def _parse_response(self, text: str) -> dict:
        """Parse JSON from LLM response, stripping markdown fences if present."""
        # Strip markdown code fences
        cleaned = re.sub(r'```(?:json)?\s*', '', text)
        cleaned = cleaned.strip().rstrip('`')

        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if not match:
            raise LLMInvalidResponseError(f"No JSON found in response", raw_response=text)

        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as e:
            raise LLMInvalidResponseError(f"Invalid JSON: {e}", raw_response=text)


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY C: OLLAMA NORMALIZER (Local LLM)
# ══════════════════════════════════════════════════════════════════════════════

class OllamaNormalizer:
    """
    Local LLM via Ollama (llama3.2).
    Zero-cost fallback when Bedrock is unavailable.
    """

    def __init__(self):
        self.host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.model = "llama3.2"
        self.timeout = 60

    def normalize(self, raw_title: str, hints: Dict[str, Any] = None,
                  description: str = "") -> NormalizedProduct:
        """Normalize using local Ollama LLM."""
        import httpx

        hints = hints or {}
        prompt = self._build_prompt(raw_title, description, hints)

        try:
            response = httpx.post(
                f"{self.host}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.0},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()

            result = response.json()
            text = result.get("response", "")
            parsed = self._parse_response(text)

            # Merge hints
            for key in ["brand", "volume_ml", "fragrance_type", "gender"]:
                hint_key = key if key != "volume_ml" else "ml"
                if hints.get(hint_key) is not None:
                    if key == "volume_ml":
                        parsed["ml"] = hints["ml"]
                    else:
                        parsed[key] = hints[hint_key]

            return NormalizedProduct(
                brand=parsed.get("brand", "Unknown"),
                product_name=parsed.get("product_name", raw_title[:100]),
                variant=parsed.get("variant"),
                fragrance_type=parsed.get("fragrance_type"),
                volume_ml=parsed.get("ml"),
                gender=parsed.get("gender"),
                normalization_method="LLM_OLLAMA",
                confidence_score=0.75,
            )

        except httpx.HTTPError as e:
            raise LLMError(f"Ollama request failed: {e}", provider="ollama", model=self.model)
        except Exception as e:
            raise LLMError(f"Ollama normalization failed: {e}", provider="ollama", model=self.model)

    def _build_prompt(self, raw_title: str, description: str, hints: Dict[str, Any]) -> str:
        hint_lines = []
        if hints.get("brand"):
            hint_lines.append(f'Brand is CONFIRMED: "{hints["brand"]}"')
        if hints.get("ml"):
            hint_lines.append(f'Volume is CONFIRMED: {hints["ml"]} ml')
        if hints.get("fragrance_type"):
            hint_lines.append(f'Fragrance type is CONFIRMED: "{hints["fragrance_type"]}"')
        if hints.get("gender"):
            hint_lines.append(f'Gender is CONFIRMED: "{hints["gender"]}"')

        hints_block = "; ".join(hint_lines) if hint_lines else "None"

        return f"""Extract perfume data from this title as JSON.
Title: "{raw_title}"
Description: "{description[:200]}"
Confirmed: {hints_block}
Return ONLY JSON: {{"brand":"","product_name":"","variant":null,"fragrance_type":null,"ml":null,"gender":null}}"""

    def _parse_response(self, text: str) -> dict:
        cleaned = re.sub(r'```(?:json)?\s*', '', text)
        cleaned = cleaned.strip().rstrip('`')
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if not match:
            raise LLMInvalidResponseError(f"No JSON found in Ollama response", raw_response=text)
        return json.loads(match.group(0))


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY C2: OPENAI / BEDROCK NORMALIZER
# ══════════════════════════════════════════════════════════════════════════════

class OpenAIBedrockNormalizer:
    """
    Bedrock/OpenAI-compatible Normalizer using litellm.
    Automatically uses AWS credentials from .env if model starts with bedrock/.
    """

    def __init__(self):
        self.model = os.getenv("OPENAI_BEDROCK_MODEL", "bedrock/amazon.nova-lite-v1:0")

    def normalize(self, raw_title: str, hints: Dict[str, Any] = None,
                  description: str = "") -> NormalizedProduct:
        """Normalize using litellm Python SDK."""
        import litellm

        hints = hints or {}
        prompt = self._build_prompt(raw_title, description, hints)

        for attempt in range(2):
            try:
                response = litellm.completion(
                    model=self.model,
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.0,
                    # Optional: format if supported natively by litellm for the given model
                )

                extracted_text = response.choices[0].message.content
                parsed = self._parse_response(extracted_text)

                # Merge hints
                for key in ["brand", "volume_ml", "fragrance_type", "gender"]:
                    hint_key = key if key != "volume_ml" else "ml"
                    if hints.get(hint_key) is not None:
                        if key == "volume_ml":
                            parsed["ml"] = hints["ml"]
                        else:
                            parsed[key] = hints[hint_key]

                return NormalizedProduct(
                    brand=parsed.get("brand", "Unknown"),
                    product_name=parsed.get("product_name", raw_title[:100]),
                    variant=parsed.get("variant"),
                    fragrance_type=parsed.get("fragrance_type"),
                    volume_ml=parsed.get("ml"),
                    gender=parsed.get("gender"),
                    normalization_method="LLM_OPENAI_BEDROCK",
                    confidence_score=0.85,
                )

            except json.JSONDecodeError as e:
                log.warning(f"Bedrock JSON parse error (attempt {attempt + 1}): {e}")
                prompt += "\n\nCRITICAL: Output ONLY valid JSON."
            except Exception as e:
                log.error(f"Bedrock error (attempt {attempt + 1}): {e}")
                if attempt == 1:
                    raise LLMError(f"Bedrock failed after 2 attempts: {e}", provider="litellm", model=self.model)

        raise LLMError(f"Bedrock failed to return valid JSON", provider="litellm")

    def _build_prompt(self, raw_title: str, description: str, hints: Dict[str, Any]) -> str:
        hint_lines = []
        if hints.get("brand"):
            hint_lines.append(f'- Brand is CONFIRMED as: "{hints["brand"]}"')
        if hints.get("ml"):
            hint_lines.append(f'- Volume is CONFIRMED as: {hints["ml"]} ml')
        if hints.get("fragrance_type"):
            hint_lines.append(f'- Fragrance type is CONFIRMED as: "{hints["fragrance_type"]}"')
        if hints.get("gender"):
            hint_lines.append(f'- Gender is CONFIRMED as: "{hints["gender"]}"')

        hints_block = "\n".join(hint_lines) if hint_lines else "None"

        return f"""You are a perfume data normalization specialist.

Analyze the following perfume product listing and extract the fields below.

Product Title: "{raw_title}"
Description: "{description[:300]}"

Pre-confirmed values (DO NOT modify these):
{hints_block}

Extract the following JSON fields:
- brand: The perfume manufacturer/house (e.g., "Dior")
- product_name: The specific fragrance name only, NO brand, NO size, NO type
- variant: Specific edition/variation if any (e.g., "Intense") or null
- fragrance_type: ONE of: EDP, EDT, PARFUM, COLOGNE, BODY_MIST — or null
- ml: Integer volume in milliliters or null
- gender: ONE of: M, F, UNISEX

Output ONLY a single valid JSON object."""

    def _parse_response(self, text: str) -> dict:
        cleaned = re.sub(r'```(?:json)?\s*', '', text)
        cleaned = cleaned.strip().rstrip('`')
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if not match:
            raise LLMInvalidResponseError(f"No JSON found in response", raw_response=text)
        return json.loads(match.group(0))


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY D: HYBRID NORMALIZER (PRODUCTION DEFAULT)
# ══════════════════════════════════════════════════════════════════════════════

class HybridNormalizer:
    """
    Production normalizer: Regex first, LLM only for gaps.

    Pipeline:
    1. Run RegexNormalizer on the title
    2. If all critical fields resolved AND confidence >= 0.85 → return regex result (no LLM)
    3. If any critical field missing → build hints from regex, call LLM for ONLY missing fields
    4. Merge: regex-confirmed fields override LLM; LLM fills gaps
    5. On LLM failure → fallback to regex-only result

    Target: 15-25% LLM call rate.
    """

    def __init__(self, llm_provider: str = "gemini"):
        """
        Args:
            llm_provider: Which LLM to use when regex is insufficient.
                          "gemini" (default), "ollama", or "bedrock"
        """
        self.llm_provider = llm_provider
        self.regex = RegexNormalizer()
        # Lazy-initialized LLM normalizers — avoids import errors
        # when optional dependencies aren't installed
        self._ollama = None
        self._gemini = None
        self._openai = None

    @property
    def ollama(self) -> OllamaNormalizer:
        if self._ollama is None:
            self._ollama = OllamaNormalizer()
        return self._ollama

    @property
    def gemini(self) -> GeminiNormalizer:
        if self._gemini is None:
            self._gemini = GeminiNormalizer()
        return self._gemini

    @property
    def openai(self) -> OpenAIBedrockNormalizer:
        if self._openai is None:
            self._openai = OpenAIBedrockNormalizer()
        return self._openai

    def normalize(self, raw_title: str, vendor: str = None, tags: List[str] = None,
                  barcode: str = None, description: str = "") -> NormalizedProduct:
        """
        Normalize a product title using the hybrid strategy.

        Args:
            raw_title: Raw product listing title
            vendor: Brand from source metadata
            tags: Category/type tags
            barcode: EAN-13/UPC barcode
            description: Product description

        Returns:
            NormalizedProduct with the best available data
        """
        # Step 1: Regex extraction
        regex_result = self.regex.normalize(raw_title, vendor, tags, barcode)

        # Step 2: Check if regex resolved all critical fields
        critical_resolved = (
            regex_result.brand != "Unknown" and
            regex_result.product_name and
            regex_result.fragrance_type is not None and
            regex_result.volume_ml is not None
        )

        if critical_resolved and regex_result.confidence_score >= 0.85:
            log.debug(f"Regex sufficient (confidence={regex_result.confidence_score}): {raw_title[:60]}")
            return regex_result

        # Step 3: Build hints from regex results and call LLM
        hints = {}
        if regex_result.brand != "Unknown":
            hints["brand"] = regex_result.brand
        if regex_result.volume_ml:
            hints["ml"] = regex_result.volume_ml
        if regex_result.fragrance_type:
            hints["fragrance_type"] = regex_result.fragrance_type
        if regex_result.gender:
            hints["gender"] = regex_result.gender

        llm_result = None
        
        # Fallback Cascade: Ollama -> Gemini -> OpenAI/Bedrock
        try:
            llm_result = self.ollama.normalize(raw_title, hints=hints, description=description)
        except Exception as e_ollama:
            log.warning(f"Ollama fallback failed: {e_ollama}. Trying Gemini...")
            try:
                llm_result = self.gemini.normalize(raw_title, hints=hints, description=description)
            except Exception as e_gemini:
                log.warning(f"Gemini fallback failed: {e_gemini}. Trying OpenAI/Bedrock...")
                try:
                    llm_result = self.openai.normalize(raw_title, hints=hints, description=description)
                except Exception as e_openai:
                    log.error(f"All LLM fallbacks failed! OpenAI Error: {e_openai}")
                    return regex_result

        # Step 4: Merge — regex-confirmed fields override LLM
        merged = NormalizedProduct(
            brand=regex_result.brand if regex_result.brand != "Unknown" else llm_result.brand,
            product_name=llm_result.product_name or regex_result.product_name or raw_title[:100],
            variant=regex_result.variant or llm_result.variant,
            fragrance_type=regex_result.fragrance_type or llm_result.fragrance_type,
            volume_ml=regex_result.volume_ml or llm_result.volume_ml,
            gender=regex_result.gender or llm_result.gender,
            ean_13=regex_result.ean_13,
            normalization_method="HYBRID",
            confidence_score=max(regex_result.confidence_score, llm_result.confidence_score),
        )

        log.debug(f"Hybrid LLM used (method={merged.normalization_method}): {raw_title[:60]}")
        return merged



# ══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

# Default production normalizer
_default_normalizer: Optional[HybridNormalizer] = None


def get_normalizer(llm_provider: str = None) -> HybridNormalizer:
    """Get or create the default HybridNormalizer instance."""
    global _default_normalizer
    if _default_normalizer is None or (llm_provider and llm_provider != _default_normalizer.llm_provider):
        provider = llm_provider or os.getenv("LLM_PROVIDER", "bedrock")
        _default_normalizer = HybridNormalizer(llm_provider=provider)
    return _default_normalizer


def normalize_product(raw_title: str, vendor: str = None, tags: List[str] = None,
                      barcode: str = None, description: str = "") -> NormalizedProduct:
    """
    Convenience function: normalize a product title using the default HybridNormalizer.

    Args:
        raw_title: Raw product listing title
        vendor: Brand from source metadata
        tags: Category/type tags
        barcode: EAN-13/UPC barcode
        description: Product description

    Returns:
        NormalizedProduct
    """
    normalizer = get_normalizer()
    return normalizer.normalize(raw_title, vendor, tags, barcode, description)
