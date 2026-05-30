"""
AI normalization pipeline using AWS Bedrock (OpenAI GPT-oss-20b via Converse API).

Strategy for maximum data quality:
1. Pre-extract ml (milliliters) from raw_title via regex → avoids AI guessing
2. Pre-extract fragrance_type (EDP/EDT/PARFUM etc.) from title + tags via regex
3. Use vendor field as authoritative brand (bypasses AI brand inference entirely)
4. Send a tightly-constrained prompt with explicit hints to AI for remaining fields
5. Validate response via Pydantic — retry once on failure with correction prompt
"""
import boto3
from botocore.config import Config
import json
import os
import re
from typing import Optional, Dict, Any

from dotenv import load_dotenv
from .schemas import AIProductExtraction

load_dotenv()

# Configure Boto3 to handle high concurrency with AWS Bedrock via adaptive retries
bedrock_config = Config(
    retries = {
        'max_attempts': 10,
        'mode': 'adaptive'
    }
)

bedrock_client = boto3.client(
    service_name="bedrock-runtime",
    region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    config=bedrock_config
)

# ── Regex Pre-extractors ────────────────────────────────────────────────────

_ML_PATTERN = re.compile(
    r"\b(\d{2,4})\s*(?:ml|ML|Ml|mL)\b",
    re.IGNORECASE,
)

_FRAGRANCE_TYPES = {
    "EDP": ["edp", "eau de parfum", "eau de parfüm", "parfum", "parfüm"],
    "EDT": ["edt", "eau de toilette", "toilette"],
    "PARFUM": ["extrait de parfum", "pure parfum", "parfum extrait"],
    "COLOGNE": ["edc", "eau de cologne", "cologne"],
    "BODY_MIST": ["body mist", "body splash", "splash", "mist"],
}

# Tags that map to gender
_GENDER_MAP = {
    "M": ["hombre", "men", "man", "masculino", "masculine", "him", "pour homme"],
    "F": ["mujer", "women", "woman", "femenino", "feminine", "her", "pour femme", "dama"],
    "UNISEX": ["unisex", "unisexo"],
}


def _extract_ml(text: str) -> Optional[int]:
    """Extract milliliter volume from a product title."""
    match = _ML_PATTERN.search(text)
    if match:
        return int(match.group(1))
    return None


def _extract_fragrance_type(title: str, tags: list) -> Optional[str]:
    """Extract fragrance type from title or tags."""
    combined = f"{title} {' '.join(tags or [])}".lower()
    for ftype, keywords in _FRAGRANCE_TYPES.items():
        for kw in keywords:
            if kw in combined:
                return ftype
    return None


def _extract_gender(title: str, tags: list) -> Optional[str]:
    """Infer gender from tags or title keywords."""
    combined = f"{title} {' '.join(tags or [])}".lower()
    for gender, keywords in _GENDER_MAP.items():
        for kw in keywords:
            if kw in combined:
                return gender
    return None


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()[:300]


# ── Main Normalization Function ─────────────────────────────────────────────

def normalize_via_bedrock(raw_listing: Dict[str, Any]) -> dict:
    """
    Normalize a raw listing dict into a canonical product schema.

    Returns a dict compatible with AIProductExtraction + gender + ean_13.
    Never raises — returns partial data on failure.
    """
    raw_title = raw_listing.get("raw_title", "")
    vendor = raw_listing.get("vendor")            # Brand from Shopify/source metadata
    description = _strip_html(raw_listing.get("description", "") or "")
    tags = raw_listing.get("tags", []) or []
    barcode = raw_listing.get("barcode", "") or ""

    # ── Step 1: Pre-extract what we can without AI ──────────────────────
    ml = _extract_ml(raw_title)
    fragrance_type = _extract_fragrance_type(raw_title, tags)
    gender = _extract_gender(raw_title, tags)
    ean_13 = barcode if barcode and len(barcode) in (12, 13) and barcode.isdigit() else None

    # If vendor is set, we already know the brand
    known_brand = vendor.strip() if vendor and vendor.strip() else None

    # ── Step 2: Decide if AI call is needed ─────────────────────────────
    # If we have brand and product_name can be guessed, skip AI
    # Product name = title with brand and ml/type stripped out
    guessed_name = _derive_product_name(raw_title, known_brand)

    # If all critical fields resolved locally, skip Bedrock to save cost
    if known_brand and guessed_name and ml and fragrance_type:
        return {
            "brand": known_brand,
            "product_name": guessed_name,
            "variant": None,
            "fragrance_type": fragrance_type,
            "ml": ml,
            "gender": gender,
            "ean_13": ean_13,
        }

    # ── Step 3: AI call with maximum hinting ────────────────────────────
    return _call_bedrock_with_hints(
        raw_title=raw_title,
        description=description,
        known_brand=known_brand,
        known_ml=ml,
        known_fragrance_type=fragrance_type,
        known_gender=gender,
        ean_13=ean_13,
    )


def _derive_product_name(raw_title: str, brand: Optional[str]) -> Optional[str]:
    """Strip brand and size/type suffixes to get a clean product name."""
    name = raw_title

    # Remove brand prefix (case-insensitive)
    if brand:
        name = re.sub(rf"^{re.escape(brand)}\s*[-–]?\s*", "", name, flags=re.IGNORECASE)

    # Remove ml suffix
    name = _ML_PATTERN.sub("", name)

    # Remove fragrance type suffixes
    for keywords in _FRAGRANCE_TYPES.values():
        for kw in keywords:
            name = re.sub(rf"\b{re.escape(kw)}\b", "", name, flags=re.IGNORECASE)

    # Remove gender markers
    for keywords in _GENDER_MAP.values():
        for kw in keywords:
            name = re.sub(rf"\b{re.escape(kw)}\b", "", name, flags=re.IGNORECASE)

    # Clean up
    name = re.sub(r"[\(\)\[\]\-–_]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip().strip("-").strip()
    return name if len(name) >= 2 else None


def _call_bedrock_with_hints(
    raw_title: str,
    description: str,
    known_brand: Optional[str],
    known_ml: Optional[int],
    known_fragrance_type: Optional[str],
    known_gender: Optional[str],
    ean_13: Optional[str],
) -> dict:
    """Invoke Bedrock AI with all pre-computed hints included in the prompt."""

    # Build a hints block so the AI doesn't need to infer known values
    hints = []
    if known_brand:
        hints.append(f'- Brand is CONFIRMED as: "{known_brand}" — do NOT change this')
    if known_ml:
        hints.append(f'- Volume is CONFIRMED as: {known_ml} ml — do NOT change this')
    if known_fragrance_type:
        hints.append(f'- Fragrance type is CONFIRMED as: "{known_fragrance_type}" — do NOT change this')
    if known_gender:
        hints.append(f'- Gender is CONFIRMED as: "{known_gender}" — do NOT change this')

    hints_block = "\n".join(hints) if hints else "No pre-confirmed values."

    prompt = f"""You are a perfume data normalization specialist for a Chilean wholesale distributor database.

Analyze the following perfume product listing and extract the fields below.

Product Title: "{raw_title}"
Description: "{description}"

Pre-confirmed values (DO NOT modify these):
{hints_block}

Extract the following JSON fields:
- brand: The perfume manufacturer/house (e.g., "Dior", "Carolina Herrera", "Armani")
- product_name: The specific fragrance name only, NO brand name, NO size, NO type (e.g., "Sauvage", "Good Girl", "Acqua di Gio")
- variant: Specific edition/variation if any (e.g., "Intense", "Sport", "Blue Edition") or null
- fragrance_type: ONE of: EDP, EDT, PARFUM, COLOGNE, BODY_MIST — or null if unknown
- ml: Integer volume in milliliters (e.g., 100) or null if not in title
- gender: ONE of: M, F, UNISEX — based on context

Output ONLY a single valid JSON object. No markdown, no explanation, no extra text.
"""

    messages = [{"role": "user", "content": [{"text": prompt}]}]

    default_result = {
        "brand": known_brand or "Unknown",
        "product_name": raw_title[:100],
        "variant": None,
        "fragrance_type": known_fragrance_type,
        "ml": known_ml,
        "gender": known_gender,
        "ean_13": ean_13,
    }

    for attempt in range(2):
        try:
            response = bedrock_client.converse(
                modelId="anthropic.claude-3-haiku-20240307-v1:0",
                messages=messages,
                inferenceConfig={"maxTokens": 512, "temperature": 0.0},
            )

            extracted_text = ""
            for block in response["output"]["message"]["content"]:
                if "text" in block:
                    extracted_text = block["text"]
                    break

            # Extract JSON — strip markdown code fences if present
            match = re.search(r"\{.*?\}", extracted_text, re.DOTALL)
            if not match:
                raise ValueError(f"No JSON found in AI response: {extracted_text[:200]}")

            parsed = json.loads(match.group(0))

            # Merge: pre-confirmed values override AI output
            if known_brand:
                parsed["brand"] = known_brand
            if known_ml:
                parsed["ml"] = known_ml
            if known_fragrance_type:
                parsed["fragrance_type"] = known_fragrance_type
            if known_gender:
                parsed["gender"] = known_gender

            # Validate via Pydantic (strips unknown fields)
            validated = AIProductExtraction(**{
                k: parsed.get(k)
                for k in ["brand", "product_name", "variant", "fragrance_type", "ml"]
            })
            result = validated.model_dump()
            result["gender"] = parsed.get("gender") or known_gender
            result["ean_13"] = ean_13
            return result

        except json.JSONDecodeError as e:
            print(f"[AI Pipeline] JSON parse error (attempt {attempt + 1}): {e}")
            # Add a correction message and retry
            messages.append({"role": "assistant", "content": [{"text": extracted_text}]})
            messages.append({
                "role": "user",
                "content": [{"text": "Your response was not valid JSON. Output ONLY a JSON object with no other text."}]
            })
        except Exception as e:
            print(f"[AI Pipeline] Error (attempt {attempt + 1}): {e}")
            break

    print(f"[AI Pipeline] Falling back to defaults for: {raw_title}")
    return default_result