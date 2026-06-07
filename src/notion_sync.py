"""
notion_sync.py - Notion Database Sync Engine
=============================================
PURPOSE:
  Syncs stock analysis results to Notion databases.
  - Stock Analysis DB: One page per stock with full analysis
  - Portfolio Ledger DB: Tracks open positions, P&L, stop-losses
  - Watchlist DB: Stocks being monitored

SETUP:
  1. Go to https://www.notion.so/my-integrations
  2. Create a new integration (give it a name like "HSA Bot")
  3. Copy the "Internal Integration Token"
  4. In Notion, open each database -> Share -> Invite your integration
  5. Copy the Database IDs from the URL (the long hex string after the workspace name)
  6. Fill in config/notion_config.json

USAGE:
  from src.notion_sync import NotionSync
  sync = NotionSync()
  sync.upsert_stock_analysis(result_dict)
  sync.upsert_ledger_entry(position_dict)
"""

import json
import sys
from pathlib import Path
from datetime import datetime

# Force UTF-8 output on Windows console
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = Path(__file__).parent.parent
NOTION_CONFIG_FILE = BASE_DIR / "config" / "notion_config.json"


class NotionSync:
    def __init__(self):
        self.config = self._load_config()
        self.client = None
        self._connected = False
        self._try_connect()

    def _load_config(self) -> dict:
        with open(NOTION_CONFIG_FILE, "r") as f:
            return json.load(f)

    def _try_connect(self):
        """Attempt to connect to Notion API."""
        token = self.config.get("integration_token", "")
        if token == "YOUR_NOTION_INTEGRATION_TOKEN_HERE" or not token:
            print("[Notion] [!] Integration token not set. Skipping Notion sync.")
            print(f"[Notion] Edit {NOTION_CONFIG_FILE} to configure Notion.")
            return

        try:
            from notion_client import Client
            self.client = Client(auth=token)
            # Test connection
            self.client.users.me()
            self._connected = True
            print("[Notion] [OK] Connected to Notion successfully.")
        except ImportError:
            print("[Notion] [ERR] notion-client not installed. Run: pip install notion-client")
        except Exception as e:
            print(f"[Notion] [ERR] Connection failed: {e}")
            print("[Notion] Check your integration token and make sure databases are shared with the integration.")

    def is_connected(self) -> bool:
        return self._connected

    # ─────────────────────────────────────────────────────────────────────────
    # STOCK ANALYSIS DATABASE
    # ─────────────────────────────────────────────────────────────────────────

    def upsert_stock_analysis(self, result: dict) -> str | None:
        """
        Create or update a stock analysis page in Notion.
        If a page for this symbol already exists, update it.
        Returns the Notion page URL if successful.
        """
        if not self._connected:
            print("[Notion] Not connected. Skipping sync.")
            return None

        db_id = self.config["databases"]["stock_analysis"]["id"]
        if db_id == "YOUR_STOCK_ANALYSIS_DATABASE_ID":
            print("[Notion] Stock analysis database ID not configured.")
            return None

        symbol = result["symbol"]

        # Check if page exists
        existing_id = self._find_page_by_symbol(db_id, symbol)

        # Build the page content as markdown blocks
        page_content = self._build_analysis_blocks(result)

        # Build properties
        verdict_text = result.get("verdict", "INCOMPLETE").replace("✅ ", "").replace("🟡 ", "").replace("🔴 ", "").strip()
        properties = {
            "Name": {"title": [{"text": {"content": symbol}}]},
            "Symbol": {"rich_text": [{"text": {"content": symbol}}]},
            "Verdict": {"select": {"name": verdict_text}},
            "Score": {"number": result.get("score", 0)},
            "Last_Analyzed": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}},
            "Notes": {"rich_text": [{"text": {"content": f"Strengths: {', '.join(result.get('strengths', []))[:1900]}"}}]}
        }

        try:
            if existing_id:
                # Update existing page
                self.client.pages.update(page_id=existing_id, properties=properties)
                # Clear old blocks and append new ones
                old_blocks = self.client.blocks.children.list(block_id=existing_id)
                for block in old_blocks["results"]:
                    self.client.blocks.delete(block_id=block["id"])
                self.client.blocks.children.append(block_id=existing_id, children=page_content)
                page_id = existing_id
                print(f"[Notion] [OK] Updated page for {symbol}")
            else:
                # Create new page
                new_page = self.client.pages.create(
                    parent={"database_id": db_id},
                    properties=properties,
                    children=page_content
                )
                page_id = new_page["id"]
                print(f"[Notion] [OK] Created new page for {symbol}")

            return f"https://notion.so/{page_id.replace('-', '')}"

        except Exception as e:
            print(f"[Notion] [ERR] Failed to sync {symbol}: {e}")
            return None

    def _find_page_by_symbol(self, db_id: str, symbol: str) -> str | None:
        """Search database for an existing page with this symbol."""
        try:
            response = self.client.databases.query(
                database_id=db_id,
                filter={
                    "property": "Symbol",
                    "rich_text": {"equals": symbol}
                }
            )
            if response["results"]:
                return response["results"][0]["id"]
        except Exception:
            pass
        return None

    def _build_analysis_blocks(self, result: dict) -> list:
        """Convert analysis result dict to Notion block format."""
        blocks = []

        def heading(text: str, level: int = 2):
            h = {1: "heading_1", 2: "heading_2", 3: "heading_3"}[level]
            return {"type": h, h: {"rich_text": [{"text": {"content": text}}]}}

        def bullet(text: str):
            return {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": text}}]}}

        def para(text: str):
            return {"type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": text}}]}}

        def divider():
            return {"type": "divider", "divider": {}}

        # Header block
        blocks.append(heading(f"Analysis: {result['symbol']}", 1))
        blocks.append(para(f"Verdict: {result.get('verdict', 'N/A')} | Score: {result.get('score', 0)}/100"))
        blocks.append(para(f"Analyzed: {result.get('evaluated_at', '')[:19]}"))
        blocks.append(divider())

        # Score breakdown
        blocks.append(heading("Score Breakdown", 2))
        blocks.append(bullet(f"Trend Template: {result.get('trend_score', 0)}/100 (35% weight)"))
        blocks.append(bullet(f"Fundamentals: {result.get('fundamental_score', 0)}/100 (35% weight)"))
        blocks.append(bullet(f"Technicals: {result.get('technical_score', 0)}/100 (30% weight)"))
        blocks.append(divider())

        # Strengths
        if result.get("strengths"):
            blocks.append(heading("✅ Strengths", 2))
            for s in result["strengths"]:
                blocks.append(bullet(s))

        # Weaknesses
        if result.get("weaknesses"):
            blocks.append(heading("❌ Weaknesses", 2))
            for w in result["weaknesses"]:
                blocks.append(bullet(w))

        # Risk flags
        if result.get("risk_flags"):
            blocks.append(heading("⚠️ Risk Flags", 2))
            for rf in result["risk_flags"]:
                blocks.append(bullet(rf))

        # Notes
        if result.get("notes"):
            blocks.append(divider())
            blocks.append(heading("Notes", 2))
            
            for line in result["notes"].split("\n"):
                line_str = line.strip()
                if not line_str:
                    continue
                if line_str.startswith("### "):
                    blocks.append(heading(line_str[4:], 3))
                elif line_str.startswith("## "):
                    blocks.append(heading(line_str[3:], 2))
                elif line_str.startswith("# "):
                    blocks.append(heading(line_str[2:], 1))
                elif line_str.startswith("* ") or line_str.startswith("- "):
                    blocks.append(bullet(line_str[2:][:2000]))
                else:
                    blocks.append(para(line_str[:2000]))

        return blocks

    # ─────────────────────────────────────────────────────────────────────────
    # PORTFOLIO LEDGER
    # ─────────────────────────────────────────────────────────────────────────

    def upsert_ledger_entry(self, position: dict) -> str | None:
        """
        Add or update a portfolio position in the ledger.
        position = {
            "symbol": "HDFCBANK",
            "entry_price": 1800.0,
            "entry_date": "2026-06-02",
            "quantity": 10,
            "stop_loss": 1674.0,
            "target": 2178.0,
            "current_price": 1850.0,
            "status": "Open"   # Open | Closed | Stop-Hit
        }
        """
        if not self._connected:
            return None

        db_id = self.config["databases"]["portfolio_ledger"]["id"]
        if db_id == "YOUR_PORTFOLIO_LEDGER_DATABASE_ID":
            print("[Notion] Portfolio ledger database ID not configured.")
            return None

        symbol = position["symbol"]
        entry_price = position.get("entry_price", 0)
        current_price = position.get("current_price", entry_price)
        quantity = position.get("quantity", 0)
        pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price else 0

        existing_id = self._find_page_by_symbol(db_id, symbol)

        properties = {
            "Name": {"title": [{"text": {"content": symbol}}]},
            "Symbol": {"rich_text": [{"text": {"content": symbol}}]},
            "Entry_Price": {"number": entry_price},
            "Entry_Date": {"date": {"start": position.get("entry_date", datetime.now().strftime("%Y-%m-%d"))}},
            "Quantity": {"number": quantity},
            "Stop_Loss": {"number": position.get("stop_loss", 0)},
            "Target": {"number": position.get("target", 0)},
            "Current_Price": {"number": current_price},
            "PnL_Pct": {"number": round(pnl_pct, 2)},
            "Status": {"select": {"name": position.get("status", "Open")}}
        }

        try:
            if existing_id:
                self.client.pages.update(page_id=existing_id, properties=properties)
                print(f"[Notion] [OK] Ledger updated for {symbol} (P&L: {pnl_pct:+.1f}%)")
                return existing_id
            else:
                new_page = self.client.pages.create(
                    parent={"database_id": db_id},
                    properties=properties
                )
                print(f"[Notion] [OK] Ledger entry created for {symbol}")
                return new_page["id"]
        except Exception as e:
            print(f"[Notion] [ERR] Ledger sync failed for {symbol}: {e}")
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # WATCHLIST
    # ─────────────────────────────────────────────────────────────────────────

    def add_to_watchlist(self, symbol: str, notes: str = "") -> str | None:
        """Add a stock to the watchlist in Notion."""
        if not self._connected:
            return None

        db_id = self.config["databases"]["watchlist"]["id"]
        if db_id == "YOUR_WATCHLIST_DATABASE_ID":
            print("[Notion] Watchlist database ID not configured.")
            return None

        try:
            new_page = self.client.pages.create(
                parent={"database_id": db_id},
                properties={
                    "Name": {"title": [{"text": {"content": symbol}}]},
                    "Symbol": {"rich_text": [{"text": {"content": symbol}}]},
                    "Last_Analyzed": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}},
                    "Notes": {"rich_text": [{"text": {"content": notes[:1900]}}]}
                }
            )
            print(f"[Notion] [OK] {symbol} added to watchlist.")
            return new_page["id"]
        except Exception as e:
            print(f"[Notion] [ERR] Watchlist add failed: {e}")
            return None
