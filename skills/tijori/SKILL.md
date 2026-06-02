---
name: tijori-scraper
description: >
  A comprehensive, self-improving Playwright skill for extracting all available
  data from Tijori Finance (tijorifinance.com) for Indian stock analysis.
  Covers all tabs: Overview, Financials, Shareholding, Peers, Concall,
  Management, Insider, Timeline, Revenue Mix, and Operational Metrics.
  Automatically improves selectors on every exploration run.
---

# Tijori Finance Scraper Skill

## Purpose

Tijori Finance is a React+Django SPA (Single Page Application) with deep,
multi-tab stock data. Standard scraping fails because:
- Content is JavaScript-rendered
- Sessions expire (requires login)
- Many tabs lazy-load data only when clicked
- Bot detection blocks headless browsers

This skill uses a **headed Playwright browser** that:
1. Loads a saved session (so no login needed after first run)
2. Visits each tab in sequence, waits for JS to render
3. Extracts and structures all data into a clean JSON
4. Logs what it found AND what it missed (for self-improvement)
5. Updates its own selector registry on each run

---

## Tijori URL Structure

Stock pages follow this pattern:
```
https://tijorifinance.com/in/company/<SYMBOL>/
```

Sub-sections (some are URL-based, some are in-page tab clicks):
```
/in/company/<SYMBOL>/                     -> Overview (default)
/in/company/<SYMBOL>/financials/          -> P&L, Balance Sheet, Cash Flow
/in/company/<SYMBOL>/shareholding/        -> Promoter, FII, DII, MF, Public
/in/company/<SYMBOL>/peers/              -> Peer comparison table
/in/company/<SYMBOL>/concall/            -> Conference call summaries
/in/company/<SYMBOL>/management/         -> Management details
/in/company/<SYMBOL>/insider/            -> Insider trading data
/in/company/<SYMBOL>/timeline/           -> Corporate events timeline
```

Search URL: `https://tijorifinance.com/in/search?q=<SYMBOL>`

---

## Data Available Per Tab

### Tab 1: Overview
- Company name, sector, industry, BSE/NSE codes
- Current price, 52W high/low, market cap
- Key ratios: P/E, EV/EBITDA, P/B, ROE, ROCE, Debt/Equity, Dividend Yield
- Business description
- Recent stock performance (1M, 3M, 6M, 1Y returns)
- Shareholding summary (latest quarter)
- Promoter pledge status
- Reverse DCF implied growth rate (if available)

### Tab 2: Financials
Sub-tabs: P&L | Balance Sheet | Cash Flow | Ratios
- 10 years of annual data
- Last 8 quarters of quarterly data
- Toggle: Standalone vs Consolidated
- Toggle: Absolute vs Common Size vs YoY Growth
- Key P&L: Revenue, EBITDA, PAT, EPS
- Key BS: Total Assets, Debt, Cash, Book Value per share
- Key CF: Operating CF, Capex, Free Cash Flow
- Key Ratios: All profitability, leverage, efficiency ratios

### Tab 3: Shareholding
- Promoter holding % (quarterly, last 8 quarters)
- Promoter pledge % (quarterly)
- FII/FPI holding % (quarterly)
- DII holding % (quarterly)
- Mutual Fund holding % (quarterly, fund-wise breakdown)
- Public holding %
- Top 10 individual shareholders
- Changes QoQ for each category

### Tab 4: Peers
- Peer company list (same sector/industry)
- Comparative table: Market Cap, Revenue, PAT, ROE, ROCE, PE, PB
- Visual: Scatter plots / bar charts (extract underlying data)

### Tab 5: Concall
- List of all conference calls (quarterly)
- AI summary: Key management guidance points
- Highlights: Revenue guidance, margins, capex plans
- Q&A highlights

### Tab 6: Management
- Promoter names and holdings
- Board of Directors
- Key Management Personnel (KMP)
- Executive compensation trends

### Tab 7: Insider Trades
- Buy/Sell transactions by promoters/directors
- Date, entity, shares, price, transaction type
- Net buying/selling trend

### Tab 8: Timeline
- Chronological corporate events
- Results dates, AGM, board meetings
- Regulatory filings, DRHP, annual reports
- Analyst coverage updates
- Key news items

### Tab 9: Revenue Mix (Premium)
- Revenue breakdown by product segment
- Revenue breakdown by geography
- Segment margins (if disclosed)

### Tab 10: Operational Metrics (Premium)
- Industry-specific KPIs (e.g., for a bank: NIM, GNPA, NNPA, PCR)
- 6000+ operational data points across industries

---

## Script Architecture

```
skills/tijori/
├── SKILL.md                    <- This file (instructions)
├── scripts/
│   ├── explore_tijori.py       <- EXPLORER: runs on a test stock, discovers
│   │                              all selectors, saves findings to output/
│   │                              Run this when site layout changes.
│   │
│   ├── scrape_tijori.py        <- PRODUCTION SCRAPER: uses learned selectors
│   │                              from selector_registry.json to extract
│   │                              all data. Called by main scraper.
│   │
│   └── selector_registry.json  <- AUTO-UPDATED by explorer on each run.
│                                  Contains proven CSS/XPath selectors for
│                                  each data point on each tab.
│
└── output/
    └── exploration_log_<date>.json  <- Explorer findings per run
```

---

## How Self-Improvement Works

1. **Exploration Run** (`explore_tijori.py`):
   - Opens Tijori for a known stock (e.g., RELIANCE)
   - Tries MULTIPLE selector strategies for each data point
   - Records which ones succeed and their confidence score
   - Saves to `selector_registry.json` with success/failure history
   - Logs any data point it COULD NOT extract (for manual review)

2. **Production Run** (`scrape_tijori.py`):
   - Loads `selector_registry.json`
   - Uses highest-confidence selector for each data point
   - Falls back to secondary selectors if primary fails
   - Reports extraction success rate at end of run
   - If success rate drops below 80%, triggers auto re-exploration

3. **Selector Registry Format**:
```json
{
  "overview.current_price": {
    "primary": {"type": "css", "selector": "[class*='current-price']", "confidence": 0.95},
    "fallbacks": [
      {"type": "text", "selector": "Current Price", "confidence": 0.7}
    ],
    "last_verified": "2026-06-02",
    "extraction_history": [{"date": "2026-06-02", "success": true}]
  }
}
```

---

## When to Run Explorer vs Scraper

| Situation | Run |
|-----------|-----|
| First time setup | `explore_tijori.py RELIANCE` |
| Site layout changed (extraction failures > 20%) | `explore_tijori.py RELIANCE` |
| Daily stock analysis | `scrape_tijori.py HDFCBANK` |
| After Tijori updates its UI | `explore_tijori.py RELIANCE` |

---

## Antigravity Instructions

When asked to "scrape Tijori for [SYMBOL]" or "get data from Tijori for [SYMBOL]":

1. Check if `selector_registry.json` exists:
   - If NO: Run `python skills/tijori/scripts/explore_tijori.py RELIANCE` first
   - If YES: Proceed to step 2

2. Run: `python skills/tijori/scripts/scrape_tijori.py <SYMBOL>`

3. Read the output JSON from `skills/tijori/output/<SYMBOL>_<date>.json`

4. If extraction success rate < 80%:
   - Notify user that site may have changed layout
   - Run explorer again: `python skills/tijori/scripts/explore_tijori.py RELIANCE`

5. Pass the extracted data to `rule_engine.py` for analysis.

---

## Anti-Bot Strategy

Tijori has bot detection. These measures are built into both scripts:
- **Headed mode only**: Never use headless=True
- **Human-like delays**: Random waits between tab clicks (1.5-3.5 seconds)
- **Real user-agent**: Chrome 120 desktop UA string
- **Realistic viewport**: 1400x900
- **Session persistence**: Saved cookies prevent repeated logins
- **No parallel requests**: All tabs visited sequentially
- **Mouse movement simulation**: Random mouse moves before clicking tabs
- **Scroll behavior**: Page scrolled before extracting to trigger lazy loads
