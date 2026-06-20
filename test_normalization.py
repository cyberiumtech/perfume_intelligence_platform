import time
from app.normalization import (
    RegexNormalizer,
    OllamaNormalizer,
    BedrockNormalizer,
    HybridNormalizer
)
import warnings
warnings.filterwarnings('ignore')

samples = [
    # 1. Easy case: Clean, well-ordered
    "Carolina Herrera 212 VIP Rosé EDP 80ml Mujer",
    # 2. Messy case: Weird casing, swapped orders, random words
    "perfume tester DIOR sauvage 100 ML pour homme edt",
    # 3. Missing info: No volume, just brand and type
    "Bvlgari Aqua Marine eau de toilette",
    # 4. Unknown brand, hard to parse
    "Acqua di Giò Profumo 75ml EDP",
    # 5. Very noisy title with tags
    "¡OFERTA! Jean Paul Gaultier Le Male 125ml edt hombre + desodorante"
]

print("=== NORMALIZATION METHOD TESTS ===\\n")

# 1. Test REGEX
print("1. REGEX NORMALIZER")
print("-" * 50)
regex = RegexNormalizer()
t0 = time.time()
for s in samples:
    res = regex.normalize(s)
    print(f"Title : {s}")
    print(f"Result: {res.brand} | {res.product_name} | {res.fragrance_type} | {res.volume_ml}ml | Gender: {res.gender}")
    print(f"Conf  : {res.confidence_score}")
    print()
t1 = time.time()
print(f"REGEX Time: {((t1-t0)*1000):.2f} ms total\\n")

# 2. Test HYBRID (Regex + Ollama fallback)
print("2. HYBRID NORMALIZER (Regex + Ollama Fallback)")
print("-" * 50)
hybrid = HybridNormalizer(llm_provider="ollama")
t0 = time.time()
for s in samples:
    # Set a very short timeout for testing so it doesn't hang if Ollama is down
    hybrid._llm.timeout = 2.0
    res = hybrid.normalize(s)
    print(f"Title : {s}")
    print(f"Method: {res.normalization_method}")
    print(f"Result: {res.brand} | {res.product_name} | {res.fragrance_type} | {res.volume_ml}ml | Gender: {res.gender}")
    print(f"Conf  : {res.confidence_score}")
    print()
t1 = time.time()
print(f"HYBRID Time: {((t1-t0)*1000):.2f} ms total\\n")
