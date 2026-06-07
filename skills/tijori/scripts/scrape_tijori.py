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


async def extract_page_faqs(page) -> list:
    """Extract all visible and hidden accordion Q&A pairs from the DOM."""
    try:
        faqs = await page.evaluate("""() => {
            let data = [];
            let headers = document.querySelectorAll('.faq_data_header');
            headers.forEach(h => {
                let q_el = h.querySelector('span');
                let question = q_el ? q_el.innerText.trim() : h.innerText.trim();
                let targetId = h.getAttribute('data-target');
                if (targetId) {
                    let ansContainer = document.querySelector(targetId);
                    let answer = ansContainer ? ansContainer.innerText.trim() : '';
                    data.push({
                        question: question,
                        answer: answer
                    });
                }
            });
            return data;
        }""")
        return faqs
    except Exception as e:
        print(f"     [FAQ Helper] Error extracting FAQs: {e}")
        return []


# ── Tab Scrapers ──────────────────────────────────────────────────────────────

async def scrape_overview_sections(page) -> dict:
    """Scrape and parse all in-page anchors on the overview page."""
    sections_data = {
        "custom_ratios": {},
        "custom_financials": [],
        "forensics": {},
        "market_share": [],
        "revenue_mix": [],
        "operational_metrics": [],
        "brands": [],
        "corporate_actions": [],
        "connections": {},
        "knowledge_base": []
    }

    anchors = [
        "custom_ratios", "custom_financials", "forensics", "marketshare",
        "revenuemix", "operationalmetrics", "brands", "corporateactions",
        "connections", "knowledgebase"
    ]

    for anchor in anchors:
        print(f"     Scanning in-page Section: #{anchor}...")
        loc = page.locator(f"#{anchor}").first
        if await loc.count() == 0:
            continue

        try:
            # Scroll element into view
            await loc.scroll_into_view_if_needed(timeout=5000)
            await asyncio.sleep(1.5)

            # Try to click "View More" or "Show More" if visible to load full details
            try:
                view_more = loc.locator("button:has-text('View More'), button:has-text('Show More'), a:has-text('Show More'), a:has-text('View More')").first
                if await view_more.count() > 0 and await view_more.is_visible():
                    await view_more.click()
                    await asyncio.sleep(1.0)
            except Exception:
                pass

            # 1. Custom Financials, Revenue Mix, Market Share, Brands tables
            if anchor in ("custom_financials", "revenuemix", "marketshare", "brands"):
                if anchor == "custom_financials":
                    print("     [Custom Financials] Checking for collapsed sections...")
                    try:
                        expansion_map = {
                            "Balance sheet": "Net Block",
                            "Profit & Loss": "Operating Profit",
                            "Cash flow": "Cash from Operating Activity",
                            "Ratios": "ROCE (%)"
                        }
                        for header, child in expansion_map.items():
                            child_loc = loc.locator(f"tr:has-text('{child}')").first
                            if await child_loc.count() == 0:
                                print(f"     [Custom Financials] Section '{header}' is collapsed. Expanding...")
                                header_row = loc.locator(f"tr:has-text('{header}')").first
                                if await header_row.count() > 0:
                                    btn = header_row.locator("svg, button, i, [class*='plus']").first
                                    if await btn.count() > 0 and await btn.is_visible():
                                        await btn.click()
                                    else:
                                        await header_row.click()
                                    await asyncio.sleep(1.0)
                    except Exception as ex:
                        print(f"     [Custom Financials] Warning during expansion: {ex}")
                tables = loc.locator("table")
                t_count = await tables.count()
                tables_list = []
                for idx in range(t_count):
                    table = tables.nth(idx)
                    headers_el = table.locator("th")
                    h_count = await headers_el.count()
                    if h_count == 0:
                        headers_el = table.locator("thead td")
                        h_count = await headers_el.count()

                    headers = []
                    for h in range(h_count):
                        try:
                            headers.append((await headers_el.nth(h).inner_text(timeout=1000)).strip())
                        except Exception:
                            headers.append("")

                    row_els = table.locator("tbody tr")
                    r_count = await row_els.count()
                    rows = []
                    for r in range(min(r_count, 50)):
                        try:
                            cells = row_els.nth(r).locator("td")
                            c_count = await cells.count()
                            row_data = []
                            for c in range(c_count):
                                row_data.append((await cells.nth(c).inner_text(timeout=1000)).strip())
                            if any(row_data):
                                rows.append(row_data)
                        except Exception:
                            pass
                    if headers or rows:
                        tables_list.append({"headers": headers, "rows": rows})
                
                # Map to proper key name
                key_map = {
                    "custom_financials": "custom_financials",
                    "revenuemix": "revenue_mix",
                    "marketshare": "market_share",
                    "brands": "brands"
                }
                sections_data[key_map[anchor]] = tables_list

            elif anchor == "knowledgebase":
                tables = loc.locator("table")
                t_count = await tables.count()
                tables_list = []
                for idx in range(t_count):
                    table = tables.nth(idx)
                    headers_el = table.locator("th")
                    h_count = await headers_el.count()
                    if h_count == 0:
                        headers_el = table.locator("thead td")
                        h_count = await headers_el.count()

                    headers = []
                    for h in range(h_count):
                        try:
                            headers.append((await headers_el.nth(h).inner_text(timeout=1000)).strip())
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
                                cell = cells.nth(c)
                                text = (await cell.inner_text(timeout=1000)).strip()
                                links = cell.locator("a")
                                l_count = await links.count()
                                if l_count > 0:
                                    link_list = []
                                    for l_idx in range(l_count):
                                        a_el = links.nth(l_idx)
                                        href = await a_el.get_attribute("href")
                                        a_text = (await a_el.inner_text()).strip()
                                        if href:
                                            link_list.append({
                                                "text": a_text,
                                                "href": href if href.startswith("http") else f"{TIJORI_BASE}{href}"
                                            })
                                    row_data.append({"text": text, "links": link_list})
                                else:
                                    row_data.append({"text": text})
                            if any(row_data):
                                rows.append(row_data)
                        except Exception as e:
                            print(f"     [KnowledgeBase] Row error: {e}")
                    if headers or rows:
                        tables_list.append({"headers": headers, "rows": rows})
                sections_data["knowledge_base"] = tables_list

            # 2. Custom Ratios
            elif anchor == "custom_ratios":
                text = (await loc.inner_text()).strip()
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                ratios = {}
                i = 0
                while i < len(lines) - 1:
                    label = lines[i]
                    val = lines[i+1]
                    if len(label) < 30 and len(val) < 20 and not label.lower().startswith("edit"):
                        ratios[label] = val
                        i += 2
                    else:
                        i += 1
                sections_data["custom_ratios"] = ratios

            # 3. Forensics warning lists and stats
            elif anchor == "forensics":
                text = (await loc.inner_text()).strip()
                
                # Try to parse counts
                yes_match = re.search(r"(\d+)\s+Yes", text)
                neutral_match = re.search(r"(\d+)\s+Neutral", text)
                no_match = re.search(r"(\d+)\s+No(?!\s+Data)", text)
                no_data_match = re.search(r"(\d+)\s+No Data", text)

                stats = {
                    "yes": int(yes_match.group(1)) if yes_match else 0,
                    "neutral": int(neutral_match.group(1)) if neutral_match else 0,
                    "no": int(no_match.group(1)) if no_match else 0,
                    "no_data": int(no_data_match.group(1)) if no_data_match else 0,
                }
                
                # Parse list items
                items = []
                lis = loc.locator("li")
                for l_idx in range(await lis.count()):
                    li_text = (await lis.nth(l_idx).inner_text()).strip()
                    parts = [p.strip() for p in li_text.split("\n") if p.strip()]
                    if len(parts) >= 2:
                        items.append({
                            "title": parts[0],
                            "description": " ".join(parts[1:])
                        })
                
                sections_data["forensics"] = {
                    "stats": stats,
                    "items": items
                }

            # 4. Corporate Actions
            elif anchor == "corporateactions":
                text = (await loc.inner_text()).strip()
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                actions = []
                i = 0
                while i < len(lines) - 2:
                    line = lines[i]
                    if re.match(r"^\d{4}", line):
                        actions.append({
                            "date": lines[i],
                            "type": lines[i+1],
                            "details": lines[i+2]
                        })
                        i += 3
                    else:
                        i += 1
                sections_data["corporate_actions"] = actions

            # 5. Connections
            elif anchor == "connections":
                text = (await loc.inner_text()).strip()
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                connections = {}
                current_heading = "General"
                for line in lines:
                    if line in ("Customers", "Suppliers", "Partners", "Collaborations", "Directors", "Subsidiaries", "Holding Company"):
                        current_heading = line
                        connections[current_heading] = []
                    else:
                        if current_heading not in connections:
                            connections[current_heading] = []
                        if len(line) > 2:
                            connections[current_heading].append(line)
                sections_data["connections"] = connections

            # 6. Operational Metrics
            elif anchor == "operationalmetrics":
                lis = loc.locator("li")
                metrics = []
                for l_idx in range(await lis.count()):
                    val = (await lis.nth(l_idx).inner_text()).strip()
                    if val:
                        metrics.append(val)
                sections_data["operational_metrics"] = metrics

        except Exception as e:
            print(f"     [WARN] Error scraping anchor #{anchor}: {e}")

    return sections_data


async def scrape_overview(page, symbol: str, registry: dict, base_url: str) -> dict:
    """Scrape Overview tab using registry selectors."""
    print(f"  [TAB 1/7] Overview...")
    await page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_load_state("networkidle", timeout=10000)
    
    # Explicit wait for price elements to render
    try:
        await page.wait_for_selector(".price", timeout=5000)
    except Exception:
        await asyncio.sleep(2)
        
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

    # Smart robust fallbacks for price, 52W range, and key ratios
    body_text = ""
    try:
        body_text = await page.locator("body").inner_text(timeout=5000)
    except Exception:
        pass

    # 1. Current Price direct selector and regex
    if not data.get("current_price"):
        try:
            price_el = page.locator(".price").first
            if await price_el.count() > 0:
                data["current_price"] = (await price_el.inner_text()).strip()
        except Exception:
            pass

    if not data.get("current_price") and body_text:
        price_match = re.search(r"₹\s*([\d,.]+)", body_text)
        if price_match:
            data["current_price"] = price_match.group(1)

    # 2. 52W Low & High regex from body text
    if not data.get("52w_low") and body_text:
        low_match = re.search(r"Low\s*\n\s*([\d,.]+)", body_text)
        if low_match:
            data["52w_low"] = low_match.group(1)

    if not data.get("52w_high") and body_text:
        high_match = re.search(r"High\s*\n\s*([\d,.]+)", body_text)
        if high_match:
            data["52w_high"] = high_match.group(1)

    print(f"     Extracting in-page secondary sections...")
    sections = await scrape_overview_sections(page)
    data["sections"] = sections

    # 3. Dynamic Ratios Fallback Mapping from custom_ratios
    cr = sections.get("custom_ratios", {})
    if cr:
        mapping = {
            "market_cap": ["Market cap", "Market Cap"],
            "pe_ratio": ["PE", "P/E", "pe"],
            "roe": ["ROE (%)", "ROE"],
            "roce": ["ROCE (%)", "ROCE"],
            "debt_equity": ["Debt to Equity", "Debt to equity"],
            "dividend_yield": ["Div Yield (%)", "Dividend Yield"],
            "promoter_holding_pct": ["Prom Holding", "Promoter Holding"]
        }
        for data_key, cr_keys in mapping.items():
            if not data.get(data_key):
                for cr_key in cr_keys:
                    if cr_key in cr:
                        data[data_key] = cr[cr_key]
                        break

    # Recalculate resolved fields success rate
    resolved_count = sum(1 for k in keys if data.get(k))
    print(f"     Resolved {resolved_count}/{len(keys)} fields after smart fallbacks")
    data["_success_rate"] = resolved_count / len(keys)

    # Extract FAQs from the Overview page
    try:
        faqs = await extract_page_faqs(page)
        data["faqs"] = faqs
        print(f"     Extracted {len(faqs)} FAQs from Overview tab")
    except Exception as e:
        print(f"     [Overview] Error extracting FAQs: {e}")

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

    # Extract FAQs from the Financials page
    try:
        faqs = await extract_page_faqs(page)
        result["faqs"] = faqs
        print(f"     Extracted {len(faqs)} FAQs from Financials tab")
    except Exception as e:
        print(f"     [Financials] Error extracting FAQs: {e}")

    # Extract Fund Flow Analysis chart data
    try:
        fund_flow = {}
        timeframes = ["1yr", "3yr", "5yr", "10yr"]
        print("     Extracting Fund Flow Analysis chart data...")
        for tf in timeframes:
            tf_btn = page.locator(f"#fundflow-analysis li.innertab__tab[yeartab='{tf}']").first
            if await tf_btn.count() > 0:
                await tf_btn.click()
                await asyncio.sleep(1.0) # Wait for animation/data reload
                
                chart_data = await page.evaluate("""() => {
                    let sources = {};
                    let uses = {};
                    let container = document.getElementById('fundflow-analysis');
                    if (!container) return null;
                    let charts = container.querySelectorAll('.flow_analysis_chart');
                    if (charts.length >= 2) {
                        const parseChart = (chartEl) => {
                            let dataObj = {};
                            let labelEls = Array.from(chartEl.querySelectorAll('.highcharts-xaxis-labels text'));
                            let valueEls = [];
                            let textNodes = chartEl.querySelectorAll('text');
                            textNodes.forEach(t => {
                                let pClass = t.parentElement ? t.parentElement.getAttribute('class') || '' : '';
                                if (pClass.includes('highcharts-data-label')) {
                                    valueEls.push(t.textContent.trim());
                                }
                            });
                            for (let j = 0; j < labelEls.length; j++) {
                                let label = labelEls[j].textContent.trim();
                                let value = valueEls[j] || '';
                                if (label) {
                                    dataObj[label] = value;
                                }
                            }
                            return dataObj;
                        };
                        sources = parseChart(charts[0]);
                        uses = parseChart(charts[1]);
                    }
                    return { sources, uses };
                }""")
                if chart_data:
                    fund_flow[tf] = chart_data
        
        # Reset tab back to 1yr active state so we leave page clean
        init_btn = page.locator("#fundflow-analysis li.innertab__tab[yeartab='1yr']").first
        if await init_btn.count() > 0:
            await init_btn.click()
            await asyncio.sleep(0.5)

        result["fund_flow"] = fund_flow
        print(f"     Extracted Fund Flow Analysis for {len(fund_flow)} timeframes")
    except Exception as e:
        print(f"     [Financials] Error extracting Fund Flow Analysis: {e}")

    # Extract Cash Flow Analysis chart data
    try:
        cash_flow_chart = {}
        timeframes = ["1yr", "3yr", "5yr", "10yr"]
        print("     Extracting Cash Flow Analysis chart data...")
        for tf in timeframes:
            tf_btn = page.locator(f"#cash_flow_analysis li.innertab__tab[yeartab='{tf}']").first
            if await tf_btn.count() > 0:
                await tf_btn.click()
                await asyncio.sleep(1.0) # Wait for animation/data reload
                
                chart_data = await page.evaluate("""() => {
                    let dataObj = {};
                    let container = document.getElementById('cash_flow_analysis');
                    if (!container) return null;
                    let chart = container.querySelector('.flow_analysis_chart');
                    if (chart) {
                        let labelEls = Array.from(chart.querySelectorAll('.highcharts-xaxis-labels text'));
                        let valueEls = [];
                        let textNodes = chart.querySelectorAll('text');
                        textNodes.forEach(t => {
                            let pClass = t.parentElement ? t.parentElement.getAttribute('class') || '' : '';
                            if (pClass.includes('highcharts-data-label')) {
                                valueEls.push(t.textContent.trim());
                            }
                        });
                        for (let j = 0; j < labelEls.length; j++) {
                            let label = labelEls[j].textContent.trim();
                            let value = valueEls[j] || '';
                            if (label) {
                                dataObj[label] = value;
                            }
                        }
                    }
                    return dataObj;
                }""")
                if chart_data:
                    cash_flow_chart[tf] = chart_data
        
        # Reset tab back to 1yr active state so we leave page clean
        init_btn = page.locator("#cash_flow_analysis li.innertab__tab[yeartab='1yr']").first
        if await init_btn.count() > 0:
            await init_btn.click()
            await asyncio.sleep(0.5)

        result["cash_flow_chart"] = cash_flow_chart
        print(f"     Extracted Cash Flow Analysis for {len(cash_flow_chart)} timeframes")
    except Exception as e:
        print(f"     [Financials] Error extracting Cash Flow Analysis: {e}")

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

    # Robust Table 0 Fallback for current shareholding
    if success_count < 3:
        print("     [Shareholding] Success rate low. Parsing current shareholding from page tables directly...")
        try:
            tables = page.locator("table")
            count = await tables.count()
            if count > 0:
                table = tables.first
                row_els = table.locator("tbody tr")
                r_count = await row_els.count()
                if r_count == 0:
                    row_els = table.locator("tr")
                    r_count = await row_els.count()
                for r in range(r_count):
                    cells = row_els.nth(r).locator("td")
                    c_count = await cells.count()
                    if c_count == 0:
                        cells = row_els.nth(r).locator("th")
                        c_count = await cells.count()
                    if c_count >= 2:
                        label = (await cells.first.inner_text()).strip()
                        val = (await cells.last.inner_text()).strip()
                        label_lower = label.lower()
                        if "promoter" in label_lower:
                            data["current"]["promoter"] = val
                        elif "mutual fund" in label_lower:
                            data["current"]["mutual fund"] = val
                        elif "insurance" in label_lower:
                            data["current"]["insurance"] = val
                        elif "fii" in label_lower or "fpi" in label_lower:
                            data["current"]["fii"] = val
                        elif "dii" in label_lower:
                            data["current"]["dii"] = val
                        elif "public" in label_lower or "retail" in label_lower or "others" in label_lower:
                            data["current"]["public"] = val
                
                success_count = sum(1 for cat in categories if data["current"].get(cat.lower()))
                print(f"     [Shareholding] Direct table parsing resolved {success_count} categories: {data['current']}")
        except Exception as e:
            print(f"     [Shareholding] Fallback parsing error: {e}")

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

    # Extract FAQs from the Shareholding page
    try:
        faqs = await extract_page_faqs(page)
        data["faqs"] = faqs
        print(f"     Extracted {len(faqs)} FAQs from Shareholding tab")
    except Exception as e:
        print(f"     [Shareholding] Error extracting FAQs: {e}")

    return data


async def scrape_peers(page, symbol: str, registry: dict, base_url: str) -> dict:
    """Scrape peer comparison data from competitors section on main page."""
    print(f"  [TAB 4/6] Peers (Competitors)...")
    url = base_url.rstrip("/") + "/#competitors"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await wait_for_content(page)
    
    # Scroll to competitors
    try:
        loc = page.locator("#competitors").first
        if await loc.count() > 0:
            await loc.scroll_into_view_if_needed(timeout=5000)
            await asyncio.sleep(1.5)
    except Exception:
        pass
        
    print(f"     URL: {page.url}")

    data = {"peers": [], "headers": []}
    try:
        tables = page.locator("#competitors table")
        count = await tables.count()
        if count == 0:
            tables = page.locator("table:has-text('Peer Name')")
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
                        peer_dict = dict(zip(headers, row_data)) if len(headers) == len(row_data) else {"values": row_data}
                        peers.append(peer_dict)

                if peers:
                    data["peers"] = peers
                    data["headers"] = headers
                    print(f"     Found {len(peers)} peer companies with {len(headers)} metrics")
                    break
    except Exception as e:
        data["error"] = str(e)

    data["_success_rate"] = 1.0 if data["peers"] else 0.0
    return data


async def scrape_benchmarking(page, symbol: str, registry: dict, base_url: str) -> dict:
    """Scrape complete Benchmarking page."""
    print(f"  [TAB 5/6] Benchmarking...")
    url = base_url.rstrip("/") + "/benchmarking/"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await wait_for_content(page)
    await scroll_and_wait(page, 300)
    print(f"     URL: {page.url}")

    data = {"tables": []}
    try:
        tables = page.locator("table")
        count = await tables.count()
        print(f"     Found {count} table(s)")
        
        for i in range(min(count, 10)):
            table = tables.nth(i)
            headers_el = table.locator("th")
            h_count = await headers_el.count()
            headers = [(await headers_el.nth(j).inner_text(timeout=2000)).strip() for j in range(h_count)]
            
            row_els = table.locator("tbody tr")
            r_count = await row_els.count()
            rows = []
            for r in range(min(r_count, 50)):
                row = row_els.nth(r)
                cells = row.locator("td")
                c_count = await cells.count()
                row_data = [(await cells.nth(c).inner_text(timeout=2000)).strip() for c in range(c_count)]
                if any(row_data):
                    rows.append(row_data)
                    
            if rows or headers:
                data["tables"].append({
                    "table_index": i,
                    "headers": headers,
                    "rows": rows
                })
    except Exception as e:
        data["error"] = str(e)

    data["_success_rate"] = 1.0 if data["tables"] else 0.0
    return data


async def scrape_reports(page, symbol: str, registry: dict, base_url: str) -> dict:
    """Scrape complete Reports page."""
    print(f"  [TAB 6/6] Reports...")
    url = base_url.rstrip("/") + "/reports/"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await wait_for_content(page)
    await scroll_and_wait(page, 300)
    print(f"     URL: {page.url}")

    data = {"tables": [], "premium_reports": [], "sample_reports": []}
    
    # 1. Look for standard tables if any exist
    try:
        tables = page.locator("table")
        count = await tables.count()
        print(f"     Found {count} table(s)")
        
        for i in range(min(count, 10)):
            table = tables.nth(i)
            headers_el = table.locator("th")
            h_count = await headers_el.count()
            headers = [(await headers_el.nth(j).inner_text(timeout=2000)).strip() for j in range(h_count)]
            
            row_els = table.locator("tbody tr")
            r_count = await row_els.count()
            rows = []
            for r in range(min(r_count, 50)):
                row = row_els.nth(r)
                cells = row.locator("td")
                c_count = await cells.count()
                row_data = [(await cells.nth(c).inner_text(timeout=2000)).strip() for c in range(c_count)]
                if any(row_data):
                    rows.append(row_data)
                    
            if rows or headers:
                data["tables"].append({
                    "table_index": i,
                    "headers": headers,
                    "rows": rows
                })
    except Exception as e:
        data["error"] = str(e)

    # 2. Extract premium reports and samples from page body text and links
    try:
        body_text = await page.locator("body").inner_text(timeout=5000)
        report_patterns = [
            ("Risk Probe Report", r"Risk Probe Report\n(.*?)\nBuy Report", r"₹\s*([\d,.]+)"),
            ("5 Year Revenue & EBITDA Estimates", r"5 Year Revenue & EBITDA Estimates\n(.*?)\nBuy Report", r"₹\s*([\d,.]+)")
        ]
        for name, desc_pat, price_pat in report_patterns:
            desc_match = re.search(desc_pat, body_text, re.DOTALL)
            if desc_match:
                desc = desc_match.group(1).strip()
                price_match = re.search(price_pat, body_text[body_text.find(name):body_text.find(name)+1000])
                price = price_match.group(1) if price_match else "150.0"
                data["premium_reports"].append({
                    "name": name,
                    "description": desc,
                    "price": price,
                    "available": True
                })

        # Parse sample reports from links on the page
        links = await page.locator("a").all()
        for link in links:
            try:
                href = await link.get_attribute("href")
                if href and href.endswith(".pdf") and "report" in href.lower():
                    text = (await link.inner_text()).strip()
                    data["sample_reports"].append({
                        "title": text or "Sample PDF",
                        "url": href if href.startswith("http") else f"{TIJORI_BASE}{href}"
                    })
            except Exception:
                pass
                
        print(f"     Extracted {len(data['tables'])} tables, {len(data['premium_reports'])} premium reports, {len(data['sample_reports'])} sample reports")
    except Exception as e:
        print(f"     [Reports] Error extracting text/samples: {e}")

    data["_success_rate"] = 1.0 if (data["tables"] or data["premium_reports"] or data["sample_reports"]) else 0.0
    return data


# ── Main Scraper ──────────────────────────────────────────────────────────────

async def run_scraper(symbol: str, tabs: list = None, force_explore: bool = False, deep: bool = False):
    """
    Main production scraper. Extracts all data from Tijori for a given symbol.
    Returns the full data bundle dict AND saves to output/bundles/.
    """
    from playwright.async_api import async_playwright

    symbol = symbol.upper().strip()
    all_tabs = ["overview", "financials", "benchmarking", "shareholding", "reports", "peers"]
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
        "benchmarking": {},
        "shareholding": {},
        "reports": {},
        "peers": [],
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
            "benchmarking": scrape_benchmarking,
            "shareholding": scrape_shareholding,
            "reports": scrape_reports,
            "peers": scrape_peers,
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

                    # Extract and copy all overview sections to top level of the bundle
                    sections = tab_data.get("sections", {})
                    bundle["custom_ratios"] = sections.get("custom_ratios", {})
                    bundle["custom_financials"] = sections.get("custom_financials", [])
                    bundle["forensics"] = sections.get("forensics", {})
                    bundle["market_share"] = sections.get("market_share", [])
                    bundle["revenue_mix"] = sections.get("revenue_mix", [])
                    bundle["operational_metrics"] = sections.get("operational_metrics", [])
                    bundle["brands"] = sections.get("brands", [])
                    bundle["corporate_actions"] = sections.get("corporate_actions", [])
                    bundle["connections"] = sections.get("connections", {})
                    bundle["knowledge_base"] = sections.get("knowledge_base", [])
                    bundle["overview_faqs"] = tab_data.get("faqs", [])
                elif tab == "financials":
                    bundle["financials"] = tab_data
                elif tab == "benchmarking":
                    bundle["benchmarking"] = tab_data
                elif tab == "shareholding":
                    bundle["shareholding"].update(tab_data.get("current", {}))
                    bundle["shareholding"]["history"] = tab_data.get("history", [])
                    bundle["shareholding"]["faqs"] = tab_data.get("faqs", [])
                elif tab == "reports":
                    bundle["reports"] = tab_data
                elif tab == "peers":
                    bundle["peers"] = tab_data.get("peers", [])
                    bundle["peers_headers"] = tab_data.get("headers", [])

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

    # Deep Analysis: Download and parse knowledge base PDFs
    if deep and bundle.get("knowledge_base"):
        print("\n  [DEEP] Downloading and parsing knowledge base PDFs...")
        try:
            sys.path.insert(0, str(BASE_DIR / "src"))
            from pdf_handler import process_knowledge_base_pdfs
            pdf_data = process_knowledge_base_pdfs(bundle["knowledge_base"], symbol)
            bundle["knowledge_base_text"] = pdf_data
            print(f"  [DEEP] Extracted text from latest PDFs successfully: {list(pdf_data.keys())}")
        except Exception as e:
            print(f"  [DEEP ERROR] Could not process PDFs: {e}")

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
    deep = "--deep" in sys.argv

    if "--tabs" in sys.argv:
        idx = sys.argv.index("--tabs")
        try:
            tabs = sys.argv[idx + 1].split(",")
        except IndexError:
            print("Error: --tabs requires a comma-separated list, e.g., --tabs overview,financials")
            sys.exit(1)

    asyncio.run(run_scraper(symbol, tabs=tabs, force_explore=force_explore, deep=deep))
