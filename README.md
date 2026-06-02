# 📈 Hybrid Stock Analyst (HSA)

A rules-based stock analysis system for the Indian equity market, running locally on your machine with **Antigravity** as the AI brain.

---

## 🏗 Architecture

```
spyder/
├── config/
│   ├── rules.json          ← All scoring rules & thresholds (edit these!)
│   └── notion_config.json  ← Notion credentials (fill in)
├── src/
│   ├── scraper.py          ← Headed Playwright scraper (Tijori + Screener fallback)
│   ├── rule_engine.py      ← Math engine: scores fundamentals, technicals, trend
│   ├── notion_sync.py      ← Syncs results to Notion databases
│   └── main.py             ← CLI orchestrator (the file you run)
├── skills/
│   └── SKILL.md            ← Antigravity skill: tells the AI how to analyze stocks
├── memory/
│   ├── stocks/             ← JSON memory per stock (analysis history)
│   └── ledger/             ← Portfolio positions ledger
├── sessions/
│   └── tijori_session.json ← Saved browser session (auto-created on first login)
└── output/
    ├── reports/            ← Markdown reports per analysis
    └── bundles/            ← Raw scraped data JSON files
```

---

## ⚡ Quick Start

### Step 1: First-time login to Tijori

This opens a browser window for you to log in manually. After logging in, the session is saved and you won't need to do this again.

```powershell
cd c:\Users\Kiran\spyder
python src/main.py analyze HDFCBANK --login
```

### Step 2: Subsequent analyses (no browser login needed)

```powershell
python src/main.py analyze HDFCBANK
python src/main.py analyze RELIANCE
python src/main.py analyze TATAMOTORS
```

### Step 3: Demo mode (no browser, uses mock data for testing)

```powershell
python src/main.py demo HDFCBANK
```

---

## 📊 Commands

| Command | Example | What it does |
|---------|---------|--------------|
| `analyze` | `python src/main.py analyze HDFCBANK` | Full analysis (scrape + score + report + Notion) |
| `analyze --portfolio` | `python src/main.py analyze HDFCBANK --portfolio 1500000` | Analysis + position sizing |
| `analyze --login` | `python src/main.py analyze HDFCBANK --login` | Force re-login to Tijori |
| `demo` | `python src/main.py demo HDFCBANK` | Demo with mock data, no browser |
| `ledger` | `python src/main.py ledger` | Show portfolio positions |
| `add-position` | `python src/main.py add-position HDFCBANK 1800 10` | Add trade to ledger |
| `watchlist` | `python src/main.py watchlist` | Show all analyzed stocks with history |

---

## 🎯 Scoring System

| Component | Weight | What it checks |
|-----------|--------|----------------|
| **Trend Template** | 35% | 8 Minervini conditions: DMA alignment, 52W high/low proximity |
| **Fundamentals** | 35% | ROE, ROCE, D/E ratio, promoter holding, EPS/sales growth |
| **Technicals** | 30% | RSI zone, breakout volume, MACD signal |

**Verdict Thresholds:**
- ✅ **BUY**: Score ≥ 70
- 🟡 **HOLD/WATCH**: Score 45–69
- 🔴 **SELL/AVOID**: Score < 45

---

## 🔧 Configuring Notion (Optional but Recommended)

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Create a new integration → copy the **Integration Token**
3. Create 3 databases in Notion: `Stock Analysis`, `Portfolio Ledger`, `Watchlist`
4. Share each database with your integration (Share → Invite)
5. Copy each database ID from the URL
6. Fill in `config/notion_config.json`

---

## 📏 Rules & Customization

All rules are in `config/rules.json`. Key things you can tune:

- `verdict_thresholds.BUY` — raise from 70 to 80 for stricter buy signals
- `fundamentals.roe.min_pct` — minimum ROE required
- `fundamentals.promoter_holding.min_pct` — minimum promoter stake
- `position_sizing.max_risk_per_trade_pct` — change from 1.5% to your preference
- `technicals.rsi.buy_zone_min` / `buy_zone_max` — adjust RSI sweet spot

---

## 🧠 Asking Antigravity to Analyze

Once you have this system set up, just ask in your Antigravity chat:
- *"Analyze HDFCBANK"*
- *"Should I hold my RELIANCE position?"*
- *"Run a demo analysis on TATAMOTORS"*
- *"Show me my portfolio ledger"*

Antigravity will read the skill file automatically and follow the 3-phase protocol.

---

## ⚠️ Disclaimer

This tool is for educational purposes only. It is NOT financial advice. Always do your own research and consult a SEBI-registered investment advisor before making investment decisions.
