import httpx
import asyncio

async def test():
    urls = [
        "https://www.eliteperfumes-distribuidor.cl/products.json?limit=5",
        "https://www.eliteperfumes-distribuidor.cl/collections/perfumes/products.json?limit=5",
        "https://lacasadelperfume.cl/tienda/?page=1"
    ]
    
    async with httpx.AsyncClient(timeout=30) as client:
        for url in urls:
            try:
                print(f"Fetching {url}")
                resp = await client.get(url)
                if 'json' in url:
                    data = resp.json()
                    prods = data.get('products', [])
                    print(f"  Got {len(prods)} products")
                    for p in prods[:2]:
                        print(f"    - {p['title']} (images: {len(p.get('images', []))})")
                else:
                    print(f"  Got status {resp.status_code}")
            except Exception as e:
                print(f"  Failed: {e}")

asyncio.run(test())
