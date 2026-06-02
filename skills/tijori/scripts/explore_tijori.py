"""
explore_tijori.py - Simple Tijori Finance Explorer
===================================================
1. Opens Tijori homepage
2. Types symbol in search bar
3. Clicks the matching result
4. Visits each tab and grabs all data
"""

import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent.parent.parent
SCRIPTS_DIR = Path(__file__).parent
OUTPUT_DIR = Path(__file__).parent.parent / "output"
SESSION_FILE = BASE_DIR / "sessions" / "tijori_session.json"
URL_MAP_FILE = SCRIPTS_DIR / "url_map.json"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)

TIJORI_HOME = "https://tijorifinance.com"


async def get_all_tables(page, tab_name):
    """Extract every table on the current page."""
    tables_data = []
    tables = page.locator("table")
    count = await tables.count()
    print(f"    [{tab_name}] {count} table(s) found")
    for i in range(min(count, 10)):
        t = tables.nth(i)
        ths = t.locator("th")
        headers = [(await ths.nth(j).inner_text(timeout=2000)).strip()
                   for j in range(await ths.count())]
        rows_el = t.locator("tr")
        rows = []
        for r in range(min(await rows_el.count(), 50)):
            cells = rows_el.nth(r).locator("td")
            row = [(await cells.nth(c).inner_text(timeout=1500)).strip()
                   for c in range(await cells.count())]
            if any(row):
                rows.append(row)
        if headers or rows:
            tables_data.append({"headers": headers, "rows": rows})
            print(f"      Table {i}: {headers[:4]} ... ({len(rows)} rows)")
    return tables_data


async def get_page_text(page):
    try:
        return (await page.locator("body").inner_text(timeout=5000)).strip()
    except Exception:
        return ""


def normalize_search_term(symbol: str) -> str:
    s = symbol.upper().strip()
    if s.endswith("BANK") and len(s) > 4:
        return s[:-4] + " BANK"
    return s


async def run_explorer(symbol: str):
    from playwright.async_api import async_playwright

    symbol = symbol.upper().strip()
    print(f"\n{'='*55}")
    print(f"  TIJORI EXPLORER -- {symbol}")
    print(f"{'='*55}")

    url_map = json.loads(URL_MAP_FILE.read_text()) if URL_MAP_FILE.exists() else {}
    findings = {"symbol": symbol, "scraped_at": datetime.now().isoformat(), "tabs": {}}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"]
        )

        ctx_args = {
            "viewport": {"width": 1400, "height": 900},
            "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36")
        }
        if SESSION_FILE.exists():
            ctx_args["storage_state"] = str(SESSION_FILE)
            print("[Session] Loaded saved session")
        else:
            print("[Session] No session - will need to log in")

        context = await browser.new_context(**ctx_args)
        page = await context.new_page()

        # ── Step 1: Go to Tijori homepage ──────────────────────
        await page.goto(TIJORI_HOME, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)

        # Login prompt if needed
        if not SESSION_FILE.exists():
            print("\n>> Log in to Tijori in the browser, then press ENTER here.")
            input("Press ENTER after login: ")
            await context.storage_state(path=str(SESSION_FILE))

        # ── Step 2: Type in search bar ─────────────────────────
        search_term = normalize_search_term(symbol)
        print(f"\n[1] Searching for '{search_term}' (normalized)...")
        search = None
        for sel in ["#search", "#search_field", "input[placeholder*='Search']"]:
            loc = page.locator(sel)
            if await loc.count() > 0:
                search = loc.first
                break
        if not search:
            raise Exception("Could not find search box on page.")

        await search.click()
        await asyncio.sleep(0.5)
        await search.fill(search_term)          # type normalized search term
        await asyncio.sleep(3)                  # wait for autocomplete dropdown

        # ── Step 3: Click the matching result ─────────────────
        print("[2] Clicking search result...")
        suggestions = page.locator(".autocomplete-suggestion")
        count = await suggestions.count()
        if count == 0:
            suggestions = page.locator("#search_results a")
            count = await suggestions.count()

        if count == 0:
            raise Exception("No search suggestions found.")

        target_idx = 0
        best_score = -1
        for i in range(count):
            text = (await suggestions.nth(i).inner_text()).strip()
            print(f"    Suggestion {i}: {text}")
            
            score = 0
            text_lower = text.lower()
            term_lower = search_term.lower()
            sym_lower = symbol.lower()
            
            if term_lower in text_lower:
                score = 100
            elif sym_lower in text_lower.replace(" ", ""):
                score = 90
            elif sym_lower[:4] in text_lower:
                score = 50
                
            if "bank" in sym_lower and "bank" in text_lower:
                score += 20
                
            if score > best_score:
                best_score = score
                target_idx = i

        best_suggestion = suggestions.nth(target_idx)
        result_text = (await best_suggestion.inner_text()).strip()
        print(f"    Clicking target suggestion [{target_idx}]: {result_text}")
        await best_suggestion.click()

        await page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(2)

        base_url = page.url.rstrip("/")
        print(f"[3] Landed on: {base_url}")
        findings["base_url"] = base_url
        url_map[symbol] = base_url
        URL_MAP_FILE.write_text(json.dumps(url_map, indent=2))

        # ── Step 4: Scrape each tab ────────────────────────────
        tabs = {
            "overview":     base_url,
            "financials":   base_url + "/financials/",
            "shareholding": base_url + "/shareholding/",
            "peers":        base_url + "/peers/",
            "concall":      base_url + "/concall/",
            "insider":      base_url + "/insider/",
            "timeline":     base_url + "/timeline/",
        }

        for tab_name, tab_url in tabs.items():
            print(f"\n[TAB] {tab_name.upper()} -> {tab_url}")
            try:
                await page.goto(tab_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(3)

                final_url = page.url
                title = await page.title()
                print(f"    Final URL : {final_url}")
                print(f"    Title     : {title}")

                # Skip if redirected away
                if "dashboard" in final_url.lower() and tab_name != "overview":
                    print(f"    [!] Redirected to dashboard — tab may need premium or different URL")
                    findings["tabs"][tab_name] = {"redirected": True, "url": final_url}
                    continue

                # Scroll to trigger lazy loading
                for _ in range(3):
                    await page.mouse.wheel(0, 600)
                    await asyncio.sleep(0.8)
                await asyncio.sleep(1.5)

                tables = await get_all_tables(page, tab_name)
                text = await get_page_text(page)

                findings["tabs"][tab_name] = {
                    "url": final_url,
                    "title": title,
                    "tables": tables,
                    "page_text_preview": text[:1000]
                }

            except Exception as e:
                print(f"    [ERROR] {tab_name}: {e}")
                findings["tabs"][tab_name] = {"error": str(e)}

        # Save session + close
        await context.storage_state(path=str(SESSION_FILE))
        await browser.close()

    # ── Save findings ──────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUTPUT_DIR / f"exploration_log_{symbol}_{ts}.json"
    out.write_text(json.dumps(findings, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n[DONE] Log saved: {out}")
    print(f"       URL map : {URL_MAP_FILE}")

    return findings


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "HDFCBANK"
    asyncio.run(run_explorer(sym))
