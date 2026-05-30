import httpx
import asyncio
import json

async def test():
    urls = [
        "https://www.eliteperfumes-distribuidor.cl/collections/perfumes/products.json?limit=250&page=1",
        "https://www.eliteperfumes-distribuidor.cl/collections/perfumes/products.json?limit=250&page=2",
        "https://lacasadelperfume.cl/tienda/?page=1",
        "https://www.multimarcasmayorista.cl/perfumes?page=1"
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
                else:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(resp.text, "html.parser")
                    if "lacasa" in url:
                        products = soup.select("li.product") or soup.select(".product-grid-item, .product-item, article.product")
                    else:
                        products = soup.select(".product-item") or soup.select(".item-product") or soup.select(".product-grid .item") or soup.select("article.product")
                    print(f"  Got {len(products)} products (status {resp.status_code})")
            except Exception as e:
                print(f"  Failed: {e}")

asyncio.run(test())
