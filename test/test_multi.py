import httpx
from bs4 import BeautifulSoup
import asyncio

async def fetch():
    url = "https://www.multimarcasmayorista.cl/perfumes?page=1"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        print("Status", resp.status_code)
        
        soup = BeautifulSoup(resp.text, "html.parser")
        products = soup.select(".product-item") or soup.select(".item-product") or soup.select(".product-grid .item") or soup.select("article.product") or soup.select(".product-block") or soup.select(".item")
        
        print(f"Products found: {len(products)}")
        if len(products) > 0:
            print("First item classes:", products[0].get("class"))
        else:
            print("No items. Here's a snippet of body:")
            print(soup.body.get_text()[:1000] if soup.body else "No body")
            
        with open("multi_out.html", "w", encoding="utf-8") as f:
            f.write(resp.text)
            
asyncio.run(fetch())
