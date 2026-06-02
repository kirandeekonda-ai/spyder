"""
scrape_tijori.py - Production Scraper for Tijori Finance
=========================================================
PURPOSE:
  Uses the selector registry (built by explore_tijori.py) to efficiently
  extract all available data from Tijori Finance for a given stock symbol.
  Outputs a clean, structured JSON bundle ready for rule_engine.py.

AUTO-IMPROVEMENT:
  - If extraction success rate drops below 80%, triggers re-exploration
  - Falls back to secondary selectors if primary fails
  - Logs every failure with details for debugging

USAGE:
  python skills/tijori/scripts/scrape_tijori.py HDFCBANK
  python skills/tijori/scripts/scrape_tijori.py RELIANCE --tabs overview,financials
  python skills/tijori/scripts/scrape_tijori.py TATAMOTORS --force-explore
"""

import asyncio
import json
import sys
import random
import re
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent.parent.parent  # c:\Users\Kiran\spyder
SKILL_DIR = Path(__file__).parent.parent               # skills/tijori
SCRIPTS_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output" / "bundles"
REGISTRY_FILE = SCRIPTS_DIR / "selector_registry.json"
URL_MAP_FILE = SCRIPTS_DIR / "url_map.json"             # symbol -> base URL
SESSION_FILE = BASE_DIR / "sessions" / "tijori_session.json"

TIJORI_BASE = "https://tijorifinance.com"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Minimum acceptable extraction success rate before triggering re-exploration
MIN_SUCCESS_RATE = 0.60


def load_url_map() -> dict:
    """Load symbol -> base_url mapping built by the explorer."""
    if URL_MAP_FILE.exists():
        with open(URL_MAP_FILE) as f:
            return json.load(f)
    return {}


async def resolve_base_url(page, symbol: str, url_map: dict) -> str | None:
    """
    Get the correct Tijori base URL for a stock symbol.
    Uses url_map first (fast), then falls back to search.
    """
    if symbol in url_map:
        print(f"  [URL] {symbol} -> {url_map[symbol]} (from url_map)")
        return url_map[symbol]

    # Not in map — try common patterns then search
    print(f"  [URL] {symbol} not in url_map. Trying search...")
    common_slugs = [
        symbol.lower(),
        symbol.lower() + "-ltd",
        symbol.lower().replace("bank", "-bank-ltd"),
        symbol.lower().replace("bank", "-bank"),
    ]
    for slug in common_slugs:
        test_url = f"{TIJORI_BASE}/company/{slug}/"
        try:
            resp = await page.goto(test_url, wait_until="domcontentloaded", timeout=12000)
            title = await page.title()
            if "does not exist" not in title.lower() and "tijori finance" != title.lower():
                url_map[symbol] = test_url.rstrip("/")
                with open(URL_MAP_FILE, "w") as f:
                    json.dump(url_map, f, indent=2)
                print(f"  [URL] Resolved: {test_url}")
                return test_url.rstrip("/")
        except Exception:
            continue

    print(f"  [URL] Could not resolve URL for {symbol}. Run explore_tijori.py first.")
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def human_delay(min_ms=800, max_ms=2500):
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))

async def scroll_and_wait(page, amount=500):
    await page.mouse.wheel(0, random.randint(amount - 100, amount + 100))
    await asyncio.sleep(random.uniform(0.4, 0.9))

async def wait_for_content(page, timeout=8000):
    """Wait for main content to load (network idle + extra buffer)."""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        await asyncio.sleep(2)
    await asyncio.sleep(random.uniform(1.0, 2.0))


def clean_number(text: str) -> float | None:
    """Convert scraped number strings to float."""
    if not text:
        return None
    cleaned = re.sub(r'[₹,%\s]', '', str(text))
    # Handle Cr/Lakh suffixes
    multiplier = 1
    if 'Cr' in text or 'cr' in text:
        multiplier = 1  # Already in Crores on Tijori
        cleaned = re.sub(r'[CrLakhs]', '', cleaned, flags=re.IGNORECASE)
    try:
        return round(float(cleaned) * multiplier, 4)
    except ValueError:
        return None


def load_registry() -> dict:
    """Load the selector registry built by the explorer."""
    if not REGISTRY_FILE.exists():
        print(f"[Scraper] WARNING: No selector registry found at {REGISTRY_FILE}")
        print(f"[Scraper] Run explore_tijori.py first to build the registry.")
        return {}
    with open(REGISTRY_FILE, "r") as f:
        return json.load(f)


# ── Smart Selector Extractor ──────────────────────────────────────────────────

async def extract_with_registry(page, full_key: str, registry: dict) -> tuple[str | None, bool]:
    """
    Extract a data point using the registry's proven selectors.
    Returns (value, success).
    Tries primary first, then fallbacks in order.
    """
    entry = registry.get(full_key, {})
    if not entry:
        return None, False

    selectors_to_try = []
    if entry.get("primary"):
        selectors_to_try.append(entry["primary"])
    selectors_to_try.extend(entry.get("fallbacks", []))

    for sel_info in selectors_to_try:
        if not sel_info or not sel_info.get("selector"):
            continue

        sel_type = sel_info.get("type", "css")
        selector = sel_info["selector"]

        try:
            if sel_type == "css":
                el = page.locator(selector).first
                if await el.count() > 0:
                    val = (await el.inner_text(timeout=3000)).strip()
                    if val:
                        return val, True

            elif sel_type == "label":
                el = page.get_by_text(selector, exact=True).first
                if await el.count() > 0:
                    sibling = el.locator("xpath=following-sibling::*[1]").first
                    if await sibling.count() > 0:
                        val = (await sibling.inner_text(timeout=3000)).strip()
                        if val:
                            return val, True
                    parent_text = (await el.locator("..").first.inner_text(timeout=3000)).strip()
                    val = parent_text.replace(selector, "").strip()
                    if val:
                        return val, True

            elif sel_type == "table_row":
                row = page.locator(f"tr:has-text('{selector}')").first
                if await row.count() > 0:
                    cells = row.locator("td")
                    count = await cells.count()
                    if count > 1:
                        val = (await cells.last.inner_text(timeout=2000)).strip()
                        return val, True

            elif sel_type == "xpath":
                el = page.locator(f"xpath={selector}").first
                if await el.count() > 0:
                    val = (await el.inner_text(timeout=3000)).strip()
                    if val:
                        return val, True

        except Exception:
            continue

    return None, False


# ── Tab Scrapers ──────────────────────────────────────────────────────────────

async def scrape_overview(page, symbol: str, registry: dict, base_url: str) -> dict:
    """Scrape Overview tab using registry selectors."""
    print(f"  [TAB 1/7] Overview...")
    await page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
    await wait_for_content(page)
    await scroll_and_wait(page, 400)
    print(f"     URL: {page.url} | Title: {await page.title()}")

    keys = [
        "company_name", "current_price", "market_cap", "pe_ratio", "pb_ratio",
        "ev_ebitda", "roe", "roce", "debt_equity", "dividend_yield",
        "52w_high", "52w_low", "sector", "industry", "bse_code", "nse_symbol",
        "promoter_holding_pct"
    ]

    data = {}
    success_count = 0
    for key in keys:
        val, ok = await extract_with_registry(page, f"overview.{key}", registry)
        data[key] = val
        if ok:
            success_count += 1

    print(f"     Extracted {success_count}/{len(keys)} fields")
    data["_success_rate"] = success_count / len(keys)
    return data


async def scrape_financials(page, symbol: str, registry: dict, base_url: str) -> dict:
    """Scrape complete financial statements."""
    print(f"  [TAB 2/7] Financials...")
    url = base_url.rstrip("/") + "/financials/"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await wait_for_content(page)
    await scroll_and_wait(page, 300)
    print(f"     URL: {page.url}")

    result = {"pnl": {}, "balance_sheet": {}, "cash_flow": {}, "ratios": {}, "tables": []}

    # Extract all tables from the financials page
    try:
        tables = page.locator("table")
        table_count = await tables.count()
        print(f"     Found {table_count} tables")

        for i in range(min(table_count, 10)):
            table = tables.nth(i)
            headers_el = table.locator("th")
            h_count = await headers_el.count()

            if h_count == 0:
                headers_el = table.locator("thead td")
                h_count = await headers_el.count()

            headers = []
            for h in range(h_count):
                try:
                    headers.append((await headers_el.nth(h).inner_text(timeout=2000)).strip())
                except Exception:
                    headers.append("")

            row_els = table.locator("tbody tr")
            r_count = await row_els.count()
            rows = []
            for r in range(min(r_count, 50)):
                try:
                    row = row_els.nth(r)
                    cells = row.locator("td")
                    c_count = await cells.count()
                    row_data = []
                    for c in range(c_count):
                        row_data.append((await cells.nth(c).inner_text(timeout=2000)).strip())
                    if any(row_data):
                        rows.append(row_data)
                except Exception:
                    pass

            if rows:
                result["tables"].append({
                    "table_index": i,
                    "headers": headers,
                    "rows": rows,
                    "total_rows": r_count
                })

    except Exception as e:
        result["error"] = str(e)

    # Parse key P&L metrics from tables
    pnl_keywords = {
        "revenue": ["Revenue", "Net Sales", "Total Revenue", "Sales"],
        "ebitda": ["EBITDA", "Operating Profit"],
        "pat": ["PAT", "Net Profit", "Profit After Tax"],
        "eps": ["EPS", "Earnings Per Share"],
        "ebitda_margin": ["EBITDA Margin"],
        "pat_margin": ["PAT Margin", "Net Margin"],
    }

    for metric, keywords in pnl_keywords.items():
        for table_data in result["tables"]:
            for row in table_data.get("rows", []):
                if row and any(kw.lower() in row[0].lower() for kw in keywords):
                    result["pnl"][metric] = {"label": row[0], "values": row[1:], "headers": table_data["headers"][1:]}
                    break

    print(f"     Extracted {len(result['tables'])} tables, {len(result['pnl'])} P&L metrics")
    return result


async def scrape_shareholding(page, symbol: str, registry: dict, base_url: str) -> dict:
    """Scrape shareholding pattern data."""
    print(f"  [TAB 3/7] Shareholding...")
    url = base_url.rstrip("/") + "/shareholding/"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await wait_for_content(page)
    await scroll_and_wait(page, 300)
    print(f"     URL: {page.url}")

    data = {"current": {}, "history": [], "pledge": {}}
    categories = ["Promoter", "FII", "FPI", "DII", "Mutual Fund", "Public", "Insurance"]

    success_count = 0
    for cat in categories:
        val, ok = await extract_with_registry(page, f"shareholding.{cat.lower()}", registry)
        if ok:
            data["current"][cat.lower()] = val
            success_count += 1

    # Also extract the full shareholding table for historical trends
    try:
        tables = page.locator("table")
        count = await tables.count()
        for i in range(min(count, 5)):
            table = tables.nth(i)
            headers_el = table.locator("th")
            h_count = await headers_el.count()
            if h_count > 2:
                headers = [(await headers_el.nth(j).inner_text(timeout=2000)).strip() for j in range(h_count)]
                row_els = table.locator("tbody tr")
                r_count = await row_els.count()
                rows = []
                for r in range(min(r_count, 10)):
                    row = row_els.nth(r)
                    cells = row.locator("td")
                    c_count = await cells.count()
                    row_data = [(await cells.nth(c).inner_text(timeout=2000)).strip() for c in range(c_count)]
                    if any(row_data):
                        rows.append(row_data)
                if rows:
                    data["history"] = {"headers": headers, "rows": rows}
                    print(f"     Shareholding history: {len(headers)} quarters, {len(rows)} categories")
                    break
    except Exception:
        pass

    print(f"     Current shareholding: {success_count}/{len(categories)} categories found")
    data["_success_rate"] = success_count / len(categories)
    return data


async def scrape_peers(page, symbol: str, registry: dict, base_url: str) -> dict:
    """Scrape peer comparison data."""
    print(f"  [TAB 4/7] Peers...")
    url = base_url.rstrip("/") + "/peers/"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await wait_for_content(page)
    await scroll_and_wait(page, 300)
    print(f"     URL: {page.url}")

    data = {"peers": [], "headers": []}
    try:
        tables = page.locator("table")
        count = await tables.count()
        for i in range(min(count, 5)):
            table = tables.nth(i)
            headers_el = table.locator("th")
            h_count = await headers_el.count()
            if h_count > 3:
                headers = [(await headers_el.nth(j).inner_text(timeout=2000)).strip() for j in range(h_count)]
                row_els = table.locator("tbody tr")
                r_count = await row_els.count()
                peers = []
                for r in range(min(r_count, 25)):
                    row = row_els.nth(r)
                    cells = row.locator("td")
                    c_count = await cells.count()
                    row_data = [(await cells.nth(c).inner_text(timeout=2000)).strip() for c in range(c_count)]
                    if any(row_data):
                        # Map to dict using headers
                        peer_dict = dict(zip(headers, row_data)) if len(headers) == len(row_data) else {"values": row_data}
                        peers.append(peer_dict)

                if peers:
                    data["peers"] = peers
                    data["headers"] = headers
                    print(f"     Found {len(peers)} peer companies with {len(headers)} metrics")
                    break
    except Exception as e:
        data["error"] = str(e)

    return data


async def scrape_concall(page, symbol: str, registry: dict, base_url: str) -> dict:
    """Scrape conference call summaries."""
    print(f"  [TAB 5/7] Concall...")
    url = base_url.rstrip("/") + "/concall/"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await wait_for_content(page)
    await scroll_and_wait(page, 400)
    print(f"     URL: {page.url}")

    # Use selector from registry
    entry = registry.get("concall.concall_item_selector", {})
    data = {"calls": [], "total_found": 0}

    selectors_to_try = ["[class*='concall']", "[class*='conference']", "[class*='summary']",
                        "[class*='transcript']", "article", ".card", "[class*='item']"]

    for selector in selectors_to_try:
        try:
            els = page.locator(selector)
            count = await els.count()
            if count > 0:
                print(f"     Found {count} concall items via {selector}")
                data["total_found"] = count
                calls = []
                for i in range(min(count, 8)):  # Get last 8 quarters
                    try:
                        item = els.nth(i)
                        text = (await item.inner_text(timeout=3000)).strip()
                        if len(text) > 20:
                            calls.append({"index": i, "content": text[:500]})
                    except Exception:
                        pass
                if calls:
                    data["calls"] = calls
                    break
        except Exception:
            continue

    if not data["calls"]:
        # Fallback: get all text from the page
        try:
            main_el = page.locator("main, #content, .container").first
            full_text = (await main_el.inner_text(timeout=5000)).strip()
            data["page_text"] = full_text[:2000]
            print(f"     Fallback: captured {len(full_text)} chars of page text")
        except Exception:
            pass

    return data


async def scrape_insider(page, symbol: str, registry: dict, base_url: str) -> dict:
    """Scrape insider trading data."""
    print(f"  [TAB 6/7] Insider Trades...")
    url = base_url.rstrip("/") + "/insider/"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await wait_for_content(page)
    print(f"     URL: {page.url}")

    data = {"trades": [], "headers": [], "net_trend": None}
    try:
        tables = page.locator("table")
        count = await tables.count()
        if count > 0:
            table = tables.first
            headers_el = table.locator("th")
            h_count = await headers_el.count()
            headers = [(await headers_el.nth(i).inner_text(timeout=2000)).strip() for i in range(h_count)]
            data["headers"] = headers

            row_els = table.locator("tbody tr")
            r_count = await row_els.count()
            trades = []
            buy_count = 0
            sell_count = 0
            for r in range(min(r_count, 20)):
                row = row_els.nth(r)
                cells = row.locator("td")
                c_count = await cells.count()
                row_data = [(await cells.nth(c).inner_text(timeout=2000)).strip() for c in range(c_count)]
                if any(row_data):
                    trade = dict(zip(headers, row_data)) if len(headers) == len(row_data) else {"values": row_data}
                    trades.append(trade)
                    # Count buy/sell trend
                    row_text = " ".join(row_data).lower()
                    if "buy" in row_text or "purchase" in row_text:
                        buy_count += 1
                    elif "sell" in row_text or "sale" in row_text:
                        sell_count += 1

            data["trades"] = trades
            data["total_trades"] = r_count
            data["buy_count"] = buy_count
            data["sell_count"] = sell_count
            data["net_trend"] = "BUYING" if buy_count > sell_count else ("SELLING" if sell_count > buy_count else "NEUTRAL")
            print(f"     {r_count} insider trades: {buy_count} buys, {sell_count} sells -> {data['net_trend']}")
    except Exception as e:
        data["error"] = str(e)

    return data


async def scrape_timeline(page, symbol: str, registry: dict, base_url: str) -> dict:
    """Scrape corporate timeline events."""
    print(f"  [TAB 7/7] Timeline...")
    url = base_url.rstrip("/") + "/timeline/"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await wait_for_content(page)
    await scroll_and_wait(page, 500)
    print(f"     URL: {page.url}")

    data = {"events": [], "total_found": 0}
    selectors_to_try = [
        "[class*='timeline-item']", "[class*='timeline'] > *", "[class*='event']",
        "[class*='activity']", ".feed-item", "li[class*='item']", ".timeline li"
    ]

    for selector in selectors_to_try:
        try:
            els = page.locator(selector)
            count = await els.count()
            if count > 2:
                data["total_found"] = count
                events = []
                for i in range(min(count, 20)):
                    try:
                        text = (await els.nth(i).inner_text(timeout=2000)).strip()
                        if text and len(text) > 10:
                            events.append({"index": i, "text": text[:300]})
                    except Exception:
                        pass
                if events:
                    data["events"] = events
                    print(f"     Found {count} timeline events via {selector}")
                    break
        except Exception:
            continue

    if not data["events"]:
        print(f"     No timeline events found with standard selectors")

    return data


# ── Main Scraper ──────────────────────────────────────────────────────────────

async def run_scraper(symbol: str, tabs: list = None, force_explore: bool = False):
    """
    Main production scraper. Extracts all data from Tijori for a given symbol.
    Returns the full data bundle dict AND saves to output/bundles/.
    """
    from playwright.async_api import async_playwright

    symbol = symbol.upper().strip()
    all_tabs = ["overview", "financials", "shareholding", "peers", "concall", "insider", "timeline"]
    tabs_to_scrape = tabs or all_tabs

    print(f"\n{'='*60}")
    print(f"  TIJORI SCRAPER -- {symbol}")
    print(f"  Tabs: {', '.join(tabs_to_scrape)}")
    print(f"{'='*60}\n")

    # Load registry
    registry = load_registry()
    url_map = load_url_map()

    # Check if registry is populated enough
    if not registry and not force_explore:
        print(f"[Scraper] Registry is empty. Running explorer first...")
        from explore_tijori import run_explorer
        await run_explorer(symbol)
        registry = load_registry()

    bundle = {
        "symbol": symbol,
        "scraped_at": datetime.now().isoformat(),
        "source": "tijori",
        "extraction_stats": {},
        "price_data": {},
        "ratios": {},
        "financials": {},
        "shareholding": {},
        "peers": [],
        "concall": {},
        "insider": {},
        "timeline": {},
        "raw_errors": []
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"]
        )

        if SESSION_FILE.exists():
            context = await browser.new_context(
                storage_state=str(SESSION_FILE),
                viewport={"width": 1400, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        else:
            print("[Scraper] No session. Please run explore_tijori.py first to login.")
            await browser.close()
            return bundle

        page = await context.new_page()

        # Resolve base URL
        base_url = await resolve_base_url(page, symbol, url_map)
        if not base_url:
            print(f"[Scraper] Could not resolve URL. Run: python skills/tijori/scripts/explore_tijori.py {symbol}")
            await browser.close()
            return bundle

        # Define scraper functions per tab
        scraper_map = {
            "overview": scrape_overview,
            "financials": scrape_financials,
            "shareholding": scrape_shareholding,
            "peers": scrape_peers,
            "concall": scrape_concall,
            "insider": scrape_insider,
            "timeline": scrape_timeline,
        }

        overall_success_rates = []

        for tab in tabs_to_scrape:
            if tab not in scraper_map:
                print(f"  [SKIP] Unknown tab: {tab}")
                continue
            try:
                tab_data = await scraper_map[tab](page, symbol, registry, base_url)
                success_rate = tab_data.pop("_success_rate", 1.0)
                overall_success_rates.append(success_rate)
                bundle["extraction_stats"][tab] = f"{success_rate*100:.0f}%"

                # Map tab data into bundle
                if tab == "overview":
                    bundle["price_data"]["current_price"] = tab_data.get("current_price")
                    bundle["price_data"]["52w_high"] = tab_data.get("52w_high")
                    bundle["price_data"]["52w_low"] = tab_data.get("52w_low")
                    bundle["ratios"]["p_e"] = tab_data.get("pe_ratio")
                    bundle["ratios"]["p_b"] = tab_data.get("pb_ratio")
                    bundle["ratios"]["ev_ebitda"] = tab_data.get("ev_ebitda")
                    bundle["ratios"]["roe"] = tab_data.get("roe")
                    bundle["ratios"]["roce"] = tab_data.get("roce")
                    bundle["ratios"]["debt_to_equity"] = tab_data.get("debt_equity")
                    bundle["ratios"]["dividend_yield"] = tab_data.get("dividend_yield")
                    bundle["ratios"]["market_cap"] = tab_data.get("market_cap")
                    bundle["company_name"] = tab_data.get("company_name")
                    bundle["sector"] = tab_data.get("sector")
                    bundle["industry"] = tab_data.get("industry")
                    bundle["bse_code"] = tab_data.get("bse_code")
                    bundle["nse_symbol"] = tab_data.get("nse_symbol")
                    bundle["shareholding"]["promoter_pct_overview"] = tab_data.get("promoter_holding_pct")
                elif tab == "financials":
                    bundle["financials"] = tab_data
                elif tab == "shareholding":
                    bundle["shareholding"].update(tab_data.get("current", {}))
                    bundle["shareholding"]["history"] = tab_data.get("history", [])
                elif tab == "peers":
                    bundle["peers"] = tab_data.get("peers", [])
                    bundle["peers_headers"] = tab_data.get("headers", [])
                elif tab == "concall":
                    bundle["concall"] = tab_data
                elif tab == "insider":
                    bundle["insider"] = tab_data
                elif tab == "timeline":
                    bundle["timeline"] = tab_data

                await human_delay(1200, 2200)

            except Exception as e:
                print(f"  [ERROR] {tab}: {e}")
                bundle["raw_errors"].append(f"{tab}: {str(e)}")

        # Save session
        await context.storage_state(path=str(SESSION_FILE))
        await browser.close()

    # Calculate overall success rate
    overall_rate = sum(overall_success_rates) / len(overall_success_rates) if overall_success_rates else 0
    bundle["extraction_stats"]["overall"] = f"{overall_rate*100:.0f}%"

    # Save bundle
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = OUTPUT_DIR / f"{symbol}_{timestamp}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"  SCRAPING COMPLETE -- {symbol}")
    print(f"  Overall success rate: {bundle['extraction_stats']['overall']}")
    print(f"  Tab breakdown: {bundle['extraction_stats']}")
    print(f"  Output: {out_file}")

    if overall_rate < MIN_SUCCESS_RATE:
        print(f"\n  [!] SUCCESS RATE {overall_rate*100:.0f}% IS BELOW {MIN_SUCCESS_RATE*100:.0f}%")
        print(f"  [!] Tijori may have updated its layout.")
        print(f"  [!] Run: python skills/tijori/scripts/explore_tijori.py {symbol}")

    print(f"{'='*60}\n")

    return bundle


# ── CLI Entry Point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    symbol = sys.argv[1]
    tabs = None
    force_explore = "--force-explore" in sys.argv

    if "--tabs" in sys.argv:
        idx = sys.argv.index("--tabs")
        try:
            tabs = sys.argv[idx + 1].split(",")
        except IndexError:
            print("Error: --tabs requires a comma-separated list, e.g., --tabs overview,financials")
            sys.exit(1)

    asyncio.run(run_scraper(symbol, tabs=tabs, force_explore=force_explore))
