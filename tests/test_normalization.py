"""
Normalization pipeline tests — 20 fixtures, target ≥70% regex accuracy.

Tests the RegexNormalizer and HybridNormalizer with real Chilean perfume titles.
"""
import pytest

from app.normalization import RegexNormalizer, NormalizedProduct


# ══════════════════════════════════════════════════════════════════════════════
# 20 FIXTURE TITLES — real Chilean perfume market listings
# ══════════════════════════════════════════════════════════════════════════════

FIXTURES = [
    # (raw_title, vendor, expected_brand, expected_fragrance_type, expected_ml, description)
    ("Carolina Herrera Good Girl EDP 80ml", None, "Carolina Herrera", "EDP", 80, "Famous brand"),
    ("Dior Sauvage Eau de Toilette 100 ml", None, "Dior", "EDT", 100, "Multi-word type"),
    ("Chanel N°5 Eau de Parfum 50ml Mujer", None, "Chanel", "EDP", 50, "French accent"),
    ("VERSACE EROS EDT 200ML HOMBRE", None, "Versace", "EDT", 200, "All caps"),
    ("Jean Paul Gaultier Le Male 125 ml EDT", None, "Jean Paul Gaultier", "EDT", 125, "4-word brand"),
    ("Paco Rabanne 1 Million Parfum 100ml", None, "Paco Rabanne", "PARFUM", 100, "Numeric name"),
    ("Perfume Hugo Boss Bottled EDP 200 ml", None, "Hugo Boss", "EDP", 200, "Prefix 'Perfume'"),
    ("Calvin Klein CK One EDT 200ml Unisex", None, "Calvin Klein", "EDT", 200, "Unisex gender"),
    ("Yves Saint Laurent Black Opium EDP 90ml", None, "Yves Saint Laurent", "EDP", 90, "3-word brand"),
    ("Armani Acqua Di Gio Profumo EDP 75ml", None, "Armani", "EDP", 75, "Italian name"),
    ("Narciso Rodriguez For Her EDT 100ml", None, "Narciso Rodriguez", "EDT", 100, "2-word brand"),
    ("Tom Ford Oud Wood EDP 50ml", None, "Tom Ford", "EDP", 50, "Niche brand"),
    ("Dolce & Gabbana Light Blue EDT 100ml Mujer", None, "Dolce & Gabbana", "EDT", 100, "Ampersand brand"),
    ("Lancôme La Vie Est Belle EDP 75ml", None, "Lancôme", "EDP", 75, "Accent brand"),
    ("Issey Miyake L'Eau D'Issey Pour Homme EDT 125ml", None, "Issey Miyake", "EDT", 125, "Apostrophe name"),
    ("Jimmy Choo Man Intense EDT 100 ml", None, "Jimmy Choo", "EDT", 100, "Man keyword"),
    ("Montblanc Explorer EDP 100ml Hombre", None, "Montblanc", "EDP", 100, "Spanish gender"),
    ("Givenchy Gentleman Society EDP 100ml", None, "Givenchy", "EDP", 100, "Society line"),
    ("Burberry Her EDP 100ml Mujer", None, "Burberry", "EDP", 100, "Simple Her"),
    ("Gucci Flora Gorgeous Gardenia EDP 100ml", None, "Gucci", "EDP", 100, "Long product name"),
]


class TestRegexNormalizer:
    """Test regex normalization accuracy on 20 fixtures."""

    @pytest.fixture
    def normalizer(self):
        return RegexNormalizer()

    @pytest.mark.parametrize(
        "raw_title,vendor,expected_brand,expected_type,expected_ml,desc",
        FIXTURES,
        ids=[f[5] for f in FIXTURES],
    )
    def test_individual_fixtures(
        self, normalizer, raw_title, vendor, expected_brand, expected_type, expected_ml, desc
    ):
        result = normalizer.normalize(raw_title, vendor=vendor)

        # Log details for debugging
        print(f"\n--- {desc} ---")
        print(f"  Input:    {raw_title}")
        print(f"  Brand:    {result.brand} (expected: {expected_brand})")
        print(f"  Type:     {result.fragrance_type} (expected: {expected_type})")
        print(f"  Volume:   {result.volume_ml} (expected: {expected_ml})")
        print(f"  Name:     {result.product_name}")
        print(f"  Conf:     {result.confidence_score}")

    def test_aggregate_accuracy(self, normalizer):
        """
        Aggregate test: at least 70% of 20 fixtures must have ALL three
        critical fields (brand, fragrance_type, volume_ml) correct.
        """
        correct = 0
        total = len(FIXTURES)

        for raw_title, vendor, expected_brand, expected_type, expected_ml, desc in FIXTURES:
            result = normalizer.normalize(raw_title, vendor=vendor)

            brand_ok = result.brand.lower() == expected_brand.lower()
            type_ok = result.fragrance_type == expected_type
            ml_ok = result.volume_ml == expected_ml

            if brand_ok and type_ok and ml_ok:
                correct += 1
            else:
                print(f"  MISS [{desc}]: brand={brand_ok}, type={type_ok}, ml={ml_ok}")

        accuracy = correct / total
        print(f"\n=== Regex Accuracy: {correct}/{total} = {accuracy:.0%} ===")
        assert accuracy >= 0.70, f"Regex accuracy {accuracy:.0%} is below 70% threshold"


class TestRegexEdgeCases:
    """Test edge cases and specific extraction logic."""

    @pytest.fixture
    def normalizer(self):
        return RegexNormalizer()

    def test_vendor_field_overrides_title(self, normalizer):
        """Vendor field should be used as brand when available."""
        result = normalizer.normalize(
            "Some Random Title EDP 100ml",
            vendor="Dior",
        )
        assert result.brand == "Dior"

    def test_ean_validation_valid(self, normalizer):
        result = normalizer.normalize("Test Product EDP 50ml", barcode="1234567890123")
        assert result.ean_13 == "1234567890123"

    def test_ean_validation_invalid_chars(self, normalizer):
        result = normalizer.normalize("Test Product EDP 50ml", barcode="ABC1234567890")
        assert result.ean_13 is None

    def test_ean_validation_wrong_length(self, normalizer):
        result = normalizer.normalize("Test Product EDP 50ml", barcode="123456")
        assert result.ean_13 is None

    def test_empty_title_returns_unknown_brand(self, normalizer):
        result = normalizer.normalize("")
        assert result.brand == "Unknown"

    def test_gender_detection_hombre(self, normalizer):
        result = normalizer.normalize("Dior Sauvage EDP 100ml Hombre")
        assert result.gender == "M"

    def test_gender_detection_mujer(self, normalizer):
        result = normalizer.normalize("Chanel N°5 EDP 50ml Mujer")
        assert result.gender == "F"

    def test_gender_detection_unisex(self, normalizer):
        result = normalizer.normalize("CK One EDT 200ml Unisex")
        assert result.gender == "UNISEX"

    def test_body_mist_detection(self, normalizer):
        result = normalizer.normalize("Victoria Secret Body Mist 250ml")
        assert result.fragrance_type == "BODY_MIST"

    def test_cologne_detection(self, normalizer):
        result = normalizer.normalize("Acqua di Parma Colonia Eau de Cologne 180ml")
        assert result.fragrance_type == "COLOGNE"

    def test_confidence_score_full(self, normalizer):
        result = normalizer.normalize("Dior Sauvage EDP 100ml")
        assert result.confidence_score >= 0.75

    def test_confidence_score_low(self, normalizer):
        result = normalizer.normalize("Unknown Product")
        assert result.confidence_score < 0.50

    def test_tags_contribute_to_fragrance_type(self, normalizer):
        result = normalizer.normalize(
            "Dior Sauvage 100ml",
            tags=["eau de parfum"],
        )
        assert result.fragrance_type == "EDP"
