"""
scraper.py - Headed Playwright scraper for Tijori Finance
=========================================================
HOW IT WORKS:
1. First run: Opens a visible browser window. You manually log in to Tijori.
   The session is saved to sessions/tijori_session.json.
2. Subsequent runs: Loads the saved session. No manual login needed.
3. Scrapes all key data sections for a given stock symbol.

USAGE:
  python src/scraper.py HDFCBANK
  python src/scraper.py RELIANCE --login   # force re-login
"""

import asyncio
import json
import sys
import os
import re
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
SESSION_FILE = BASE_DIR / "sessions" / "tijori_session.json"
OUTPUT_DIR = BASE_DIR / "output" / "bundles"

TIJORI_BASE = "https://tijorifinance.com"
SCREENER_BASE = "https://www.screener.in"

# ── Helpers ──────────────────────────────────────────────────────────────────

def clean(text: str) -> str:
    """Strip whitespace and newlines from scraped text."""
    return re.sub(r'\s+', ' ', text).strip() if text else ""


def to_float(text: str) -> float | None:
    """Convert scraped number string like '₹1,234.56' or '12.5%' to float."""
    try:
        cleaned = re.sub(r'[₹,%,\s]', '', text)
        return float(cleaned)
    except (ValueError, TypeError):
        return None


# ── Core Scraper Class ────────────────────────────────────────────────────────

class TijoriScraper:
    def __init__(self, symbol: str, force_login: bool = False):
        self.symbol = symbol.upper().strip()
        self.force_login = force_login
        self.data = {
            "symbol": self.symbol,
            "scraped_at": datetime.now().isoformat(),
            "source": "tijori",
            "price_data": {},
            "financials": {},
            "ratios": {},
            "shareholding": {},
            "peers": [],
            "management": {},
            "raw_errors": []
        }

    async def run(self) -> dict:
        async with async_playwright() as p:
            # Always headed (non-headless) to avoid bot detection
            browser = await p.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized"
                ]
            )
            context = await self._load_or_create_context(browser)
            page = await context.new_page()

            print(f"\n[Scraper] Fetching data for: {self.symbol}")

            try:
                await self._scrape_tijori(page)
            except Exception as e:
                print(f"[Scraper] Tijori error: {e}")
                self.data["raw_errors"].append(f"Tijori: {str(e)}")
                # Fallback to Screener
                try:
                    await self._scrape_screener(page)
                except Exception as e2:
                    print(f"[Scraper] Screener fallback error: {e2}")
                    self.data["raw_errors"].append(f"Screener: {str(e2)}")

            # Save session after scraping (captures any refreshed cookies)
            await context.storage_state(path=str(SESSION_FILE))
            print(f"[Scraper] Session saved to {SESSION_FILE}")

            await browser.close()

        return self.data

    async def _load_or_create_context(self, browser):
        """Load saved session if it exists and we're not forcing re-login."""
        if SESSION_FILE.exists() and not self.force_login:
            print("[Scraper] Loading saved session...")
            context = await browser.new_context(
                storage_state=str(SESSION_FILE),
                viewport={"width": 1400, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        else:
            print("[Scraper] No session found. Starting fresh login flow...")
            context = await browser.new_context(
                viewport={"width": 1400, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            # Navigate to Tijori login
            await page.goto(f"{TIJORI_BASE}/login", wait_until="networkidle", timeout=30000)
            print("\n" + "="*60)
            print("ACTION REQUIRED: Please log in to Tijori Finance in the")
            print("browser window that just opened.")
            print("Once you are logged in and see the dashboard, press ENTER here.")
            print("="*60 + "\n")
            input("Press ENTER after logging in: ")
            await context.storage_state(path=str(SESSION_FILE))
            print(f"[Scraper] Session saved to {SESSION_FILE}")
            await page.close()

        return context

    async def _scrape_tijori(self, page):
        """Scrape Tijori Finance for stock data."""
        # Search for the stock
        search_url = f"{TIJORI_BASE}/stock/{self.symbol.lower()}"
        print(f"[Scraper] Navigating to: {search_url}")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)  # Let dynamic content load

        # ── Price & Basic Info ────────────────────────────────────────────────
        print("[Scraper] Extracting price data...")
        try:
            price_el = page.locator('[class*="current-price"], [class*="stock-price"], h1 + div').first
            price_text = await price_el.inner_text(timeout=5000)
            self.data["price_data"]["current_price"] = to_float(price_text)

            # 52-week high/low
            for label in ["52W High", "52W Low", "52 Week High", "52 Week Low"]:
                el = page.get_by_text(label, exact=False).first
                if await el.count() > 0:
                    parent = el.locator("..").first
                    val = clean(await parent.inner_text(timeout=3000))
                    key = "52w_high" if "high" in label.lower() else "52w_low"
                    self.data["price_data"][key] = val
        except PlaywrightTimeout:
            print("[Scraper] Warning: Price data timeout, may need re-login")
            self.data["raw_errors"].append("Price section timed out")

        # ── Financial Ratios ─────────────────────────────────────────────────
        print("[Scraper] Extracting ratios...")
        ratio_keys = [
            "P/E", "Market Cap", "EPS", "ROE", "ROCE",
            "Debt to Equity", "Current Ratio", "Dividend Yield",
            "Book Value", "Sales Growth", "Profit Growth"
        ]
        for key in ratio_keys:
            try:
                el = page.get_by_text(key, exact=True).first
                if await el.count() > 0:
                    # Get sibling value element
                    sibling = el.locator("xpath=following-sibling::*[1]").first
                    val = clean(await sibling.inner_text(timeout=3000))
                    self.data["ratios"][key.lower().replace(" ", "_").replace("/", "_")] = val
            except Exception:
                pass

        # ── Shareholding ─────────────────────────────────────────────────────
        print("[Scraper] Extracting shareholding...")
        try:
            sh_url = f"{TIJORI_BASE}/stock/{self.symbol.lower()}/shareholding"
            await page.goto(sh_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)

            # Try to get promoter, FII, DII, Public holdings
            for holder in ["Promoter", "FII", "DII", "Public"]:
                el = page.get_by_text(holder, exact=False).first
                if await el.count() > 0:
                    parent = el.locator("..").first
                    row_text = clean(await parent.inner_text(timeout=3000))
                    # Extract percentage from row text
                    pct_match = re.search(r'(\d+\.?\d*)\s*%', row_text)
                    if pct_match:
                        self.data["shareholding"][holder.lower()] = float(pct_match.group(1))
        except Exception as e:
            self.data["raw_errors"].append(f"Shareholding section: {str(e)}")

        # ── Financials (P&L) ─────────────────────────────────────────────────
        print("[Scraper] Extracting financials...")
        try:
            fin_url = f"{TIJORI_BASE}/stock/{self.symbol.lower()}/financials"
            await page.goto(fin_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)

            # Extract table data - Tijori shows quarterly/annual P&L
            tables = page.locator("table")
            table_count = await tables.count()
            all_tables = []
            for i in range(min(table_count, 5)):
                table = tables.nth(i)
                headers = []
                rows = []
                header_els = table.locator("th")
                h_count = await header_els.count()
                for h in range(h_count):
                    headers.append(clean(await header_els.nth(h).inner_text(timeout=2000)))

                row_els = table.locator("tr")
                r_count = await row_els.count()
                for r in range(1, r_count):
                    row = row_els.nth(r)
                    cells = row.locator("td")
                    c_count = await cells.count()
                    row_data = []
                    for c in range(c_count):
                        row_data.append(clean(await cells.nth(c).inner_text(timeout=2000)))
                    if row_data:
                        rows.append(row_data)
                if headers and rows:
                    all_tables.append({"headers": headers, "rows": rows})

            self.data["financials"]["tables"] = all_tables
        except Exception as e:
            self.data["raw_errors"].append(f"Financials section: {str(e)}")

        print(f"[Scraper] Tijori scraping complete for {self.symbol}")

    async def _scrape_screener(self, page):
        """Fallback: Scrape Screener.in for stock data."""
        print(f"[Scraper] Falling back to Screener.in for {self.symbol}...")
        search_url = f"{SCREENER_BASE}/company/{self.symbol}/consolidated/"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        # Extract ratios from Screener
        try:
            ratio_list = page.locator("#top-ratios li")
            count = await ratio_list.count()
            for i in range(count):
                item = ratio_list.nth(i)
                label = clean(await item.locator("span.name").inner_text(timeout=2000))
                value = clean(await item.locator("span.value, span.number").first.inner_text(timeout=2000))
                if label:
                    self.data["ratios"][label.lower().replace(" ", "_")] = value
        except Exception as e:
            self.data["raw_errors"].append(f"Screener ratios: {str(e)}")

        # Extract P&L table
        try:
            pnl_section = page.locator("#profit-loss")
            if await pnl_section.count() > 0:
                pnl_table = pnl_section.locator("table")
                headers_el = pnl_table.locator("thead th")
                h_count = await headers_el.count()
                headers = [clean(await headers_el.nth(i).inner_text(timeout=2000)) for i in range(h_count)]

                row_els = pnl_table.locator("tbody tr")
                r_count = await row_els.count()
                rows = []
                for r in range(r_count):
                    row = row_els.nth(r)
                    cells = row.locator("td")
                    c_count = await cells.count()
                    row_data = [clean(await cells.nth(c).inner_text(timeout=2000)) for c in range(c_count)]
                    if row_data:
                        rows.append(row_data)

                self.data["financials"]["pnl"] = {"headers": headers, "rows": rows}
        except Exception as e:
            self.data["raw_errors"].append(f"Screener P&L: {str(e)}")

        self.data["source"] = "screener_fallback"
        print(f"[Scraper] Screener scraping complete for {self.symbol}")

    def save(self) -> Path:
        """Save scraped data to output/bundles as JSON."""
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = OUTPUT_DIR / f"{self.symbol}_{timestamp}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        print(f"[Scraper] Data saved to: {out_file}")
        return out_file


# ── Entry Point ───────────────────────────────────────────────────────────────

async def main():
    if len(sys.argv) < 2:
        print("Usage: python src/scraper.py <SYMBOL> [--login]")
        print("Examples:")
        print("  python src/scraper.py HDFCBANK")
        print("  python src/scraper.py RELIANCE --login   (force re-login)")
        sys.exit(1)

    symbol = sys.argv[1]
    force_login = "--login" in sys.argv

    scraper = TijoriScraper(symbol, force_login=force_login)
    data = await scraper.run()
    out_path = scraper.save()

    print(f"\n[Done] Scraped {len(data.get('ratios', {}))} ratios, "
          f"{len(data.get('shareholding', {}))} shareholding entries")
    print(f"[Done] Output file: {out_path}")
    return str(out_path)


if __name__ == "__main__":
    asyncio.run(main())
