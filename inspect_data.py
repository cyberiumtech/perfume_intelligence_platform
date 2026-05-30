import json, re, sys, os

path = r"C:\Users\core3\.gemini\antigravity-ide\brain\a76c4e07-5e21-47bb-92f9-a185736347a4\.system_generated\steps\914\content.md"
content = open(path, encoding='utf-8').read()

# Find JSON start
idx = content.find('{')
if idx == -1:
    print("No JSON found")
    sys.exit(1)

try:
    data = json.loads(content[idx:])
except:
    # Truncated — find partial
    data = {"products": []}
    
prods = data.get('products', [])
print(f"Products on page 1 of /collections/perfumes/products.json: {len(prods)}")
for p in prods[:10]:
    has_img = bool(p.get("images"))
    sku = p.get("variants", [{}])[0].get("sku", "-") if p.get("variants") else "-"
    print(f"  [{p['vendor']:15}] {p['title'][:55]:55} | img:{has_img} | sku:{sku}")
