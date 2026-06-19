import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        page = await context.new_page()
        
        print("Navigating to login...")
        await page.goto("https://pdlbodega.cl/wholesale/Login.aspx")
        
        print("Filling credentials...")
        await page.fill("#MainContent_txtEmail", "kameshgurkha1991.kg@gmail.com")
        await page.fill("#MainContent_txtPassword", "123456")
        await page.click("#MainContent_cmdLogin")
        
        try:
            print("Waiting for login redirect...")
            await page.wait_for_url(lambda u: "Login" not in u and "login" not in u, timeout=15000)
            print(f"Login successful! URL: {page.url}")
        except Exception as e:
            print(f"Login timeout/error: {e}")
            print(f"Current URL: {page.url}")
        
        print("Navigating to catalog...")
        await page.goto("https://pdlbodega.cl/wholesale/CreateOrder")
        await page.wait_for_load_state("networkidle")
        # Wait for the product table to render
        try:
            await page.wait_for_selector("table.table-hover", timeout=15000)
            print("Product table found!")
        except:
            print("WARNING: Product table not found, saving page anyway")
        
        html = await page.content()
        with open("catalog.html", "w", encoding="utf-8") as f:
            f.write(html)
            
        print("Saved catalog.html.")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
