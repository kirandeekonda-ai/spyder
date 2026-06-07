import sys
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from notion_sync import NotionSync

def refresh_notion(symbol: str):
    symbol = symbol.upper()
    memory_file = BASE_DIR / "memory" / "stocks" / f"{symbol}.json"
    
    if not memory_file.exists():
        print(f"[Refresh] Error: Memory file for {symbol} does not exist.")
        sys.exit(1)
        
    with open(memory_file, "r") as f:
        mem_data = json.load(f)
        
    # Extract the last analysis scorecard
    result = mem_data.get("last_analysis", {})
    if not result:
        print("[Refresh] Error: No last_analysis scorecard found in memory.")
        sys.exit(1)
        
    # Ensure symbol is set
    result["symbol"] = symbol
    
    notion = NotionSync()
    if notion.is_connected():
        print(f"[Refresh] Syncing {symbol} to Notion with updated notes...")
        page_url = notion.upsert_stock_analysis(result)
        if page_url:
            print(f"[Refresh] Success! Notion Page: {page_url}")
        else:
            print("[Refresh] Failed to update Notion page.")
    else:
        print("[Refresh] Notion is not connected.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python src/notion_sync_refresh.py <SYMBOL>")
        sys.exit(1)
    refresh_notion(sys.argv[1])
