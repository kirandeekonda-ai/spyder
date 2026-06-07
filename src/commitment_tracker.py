"""
commitment_tracker.py - Management Promises Tracker & Credibility Evaluator
===========================================================================
PURPOSE:
  Extracts quantitative promises made by management in concalls/presentations.
  - If Gemini API key is configured, uses Gemini to extract dynamically from PDF texts.
  - Otherwise, falls back to pre-defined presets in config/commitments_preset.json.
  
  Compares the promises against actual historical financials to calculate a
  Management Credibility Score (0-100).
"""

import os
import json
import re
from pathlib import Path
from datetime import datetime

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
GEMINI_CONFIG_FILE = BASE_DIR / "config" / "gemini_config.json"
PRESETS_FILE = BASE_DIR / "config" / "commitments_preset.json"
COMMITMENTS_DIR = BASE_DIR / "memory" / "stocks"

# ── Commitment Tracker Class ─────────────────────────────────────────────────

class CommitmentTracker:
    def __init__(self, symbol: str):
        self.symbol = symbol.upper().strip()
        self.api_key = self._load_api_key()
        self.commitments = []
        self._load_commitments()

    def _load_api_key(self) -> str | None:
        """Load Gemini API Key from config."""
        if GEMINI_CONFIG_FILE.exists():
            try:
                with open(GEMINI_CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                key = config.get("api_key")
                if key and not key.startswith("YOUR_"):
                    return key
            except Exception:
                pass
        return None

    def _load_commitments(self):
        """Load commitments from memory/stocks/<SYMBOL>_commitments.json or presets."""
        mem_path = COMMITMENTS_DIR / f"{self.symbol}_commitments.json"
        
        # 1. Try memory cache first
        if mem_path.exists():
            try:
                with open(mem_path, "r", encoding="utf-8") as f:
                    self.commitments = json.load(f)
                print(f"[Tracker] Loaded {len(self.commitments)} commitments for {self.symbol} from memory cache.")
                return
            except Exception:
                pass

        # 2. Try preset fallback
        if PRESETS_FILE.exists():
            try:
                with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                    presets = json.load(f)
                self.commitments = presets.get(self.symbol, [])
                if self.commitments:
                    print(f"[Tracker] Loaded {len(self.commitments)} preset commitments for {self.symbol}.")
                    self._save_to_memory()
                    return
            except Exception as e:
                print(f"[Tracker] Error loading presets: {e}")
        
        print(f"[Tracker] No commitments found for {self.symbol}.")

    def _save_to_memory(self):
        """Save commitments to memory cache."""
        COMMITMENTS_DIR.mkdir(parents=True, exist_ok=True)
        mem_path = COMMITMENTS_DIR / f"{self.symbol}_commitments.json"
        try:
            with open(mem_path, "w", encoding="utf-8") as f:
                json.dump(self.commitments, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Tracker] Error saving commitments: {e}")

    # ── LLM Extraction ───────────────────────────────────────────────────────

    def extract_commitments_from_text(self, text: str) -> int:
        """
        Call Gemini API to extract quantitative commitments from text.
        Returns the number of commitments extracted.
        """
        if not self.api_key:
            print("[Tracker] No Gemini API key configured. Skipping LLM extraction.")
            return 0
        if not text:
            print("[Tracker] Empty text provided. Skipping LLM extraction.")
            return 0

        print(f"[Tracker] Extracting commitments for {self.symbol} using Gemini...")
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.api_key)
            
            # Setup prompt
            prompt = (
                f"Extract all quantitative, forward-looking commitments, targets, or guidance made "
                f"by the management of {self.symbol} in the following text. "
                f"Focus on metrics like Revenue/Sales, EBITDA, Net Profit/PAT, Capex, Margins (OPM/NIM), or sales volume.\n\n"
                f"Output the results STRICTLY as a raw JSON list of objects. Do not include markdown wraps or backticks. "
                f"Each object must have the following keys:\n"
                f"- 'metric': Normalized name of the metric (e.g., 'Net Sales', 'OPM (%)', 'Operating Profit', 'Profit After Tax', 'Capex')\n"
                f"- 'target_value': The numeric value promised (as a float)\n"
                f"- 'timeframe': Normalized timeframe (e.g. 'MAR'26', 'DEC'25', 'JUN'25')\n"
                f"- 'operator': 'gte' (greater than or equal) or 'lte' (less than or equal)\n"
                f"- 'promise_text': The exact or summarized sentence from the text containing the promise\n"
                f"- 'unit': The unit of the metric (e.g., 'Cr', '%')\n\n"
                f"Text:\n{text[:30000]}"  # Limit text size to prevent token limits
            )
            
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = model.generate_content(prompt)
            
            # Clean response text from markdown block quotes if present
            cleaned_text = resp.text.strip()
            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:]
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3]
            cleaned_text = cleaned_text.strip()
            
            new_commitments = json.loads(cleaned_text)
            
            if isinstance(new_commitments, list):
                # Merge with existing commitments (avoid duplicate promise texts)
                existing_texts = {c.get("promise_text") for c in self.commitments}
                added = 0
                for c in new_commitments:
                    if c.get("promise_text") not in existing_texts and c.get("metric") and c.get("target_value") and c.get("timeframe"):
                        self.commitments.append(c)
                        added += 1
                if added > 0:
                    self._save_to_memory()
                    print(f"[Tracker] Successfully extracted {added} new commitments using Gemini.")
                return added
                
        except Exception as e:
            print(f"[Tracker] [ERR] Gemini extraction failed: {e}")
        return 0

    # ── Financial Metric Matcher ─────────────────────────────────────────────

    def _get_actual_value(self, data: dict, metric: str, timeframe: str) -> float | None:
        """Heuristically retrieve the actual metric value from data bundle P&L and tables."""
        
        def clean_str(s: str) -> str:
            return "".join(c for c in s.upper() if c.isalnum())

        target_clean = clean_str(metric)
        timeframe_clean = clean_str(timeframe)

        # Helper to clean numeric string values
        def parse_float(val_str: str) -> float | None:
            if not val_str:
                return None
            try:
                cleaned = re.sub(r"[^0-9.\-]", "", str(val_str)).strip()
                return float(cleaned) if cleaned else None
            except ValueError:
                return None

        # 1. Search in financials -> pnl
        pnl = data.get("financials", {}).get("pnl", {})
        for key, pnl_item in pnl.items():
            label = pnl_item.get("label", "")
            label_clean = clean_str(label)
            # Match if target matches label (e.g. "Net Sales" matches "Sales" loosely)
            if target_clean in label_clean or label_clean in target_clean:
                headers = pnl_item.get("headers", [])
                values = pnl_item.get("values", [])
                for h, v in zip(headers, values):
                    if clean_str(h) == timeframe_clean:
                        val = parse_float(v)
                        if val is not None:
                            return val

        # 2. Search in financials -> tables (Annual/Quarterly P&L)
        tables = data.get("financials", {}).get("tables", [])
        for table in tables:
            headers = table.get("headers", [])
            rows = table.get("rows", [])
            for row in rows:
                if not row:
                    continue
                row_name = row[0]
                if isinstance(row_name, dict):
                     row_name = row_name.get("text", "")
                row_clean = clean_str(str(row_name))
                if target_clean in row_clean or row_clean in target_clean:
                    for h, v in zip(headers, row):
                        if clean_str(str(h)) == timeframe_clean:
                            val = parse_float(v)
                            if val is not None:
                                return val

        # 3. Check general ratios
        ratios = data.get("ratios", {})
        for r_name, r_val in ratios.items():
            r_clean = clean_str(r_name)
            if target_clean in r_clean or r_clean in r_clean:
                val = parse_float(r_val)
                if val is not None:
                    return val

        return None

    # ── Evaluation ───────────────────────────────────────────────────────────

    def evaluate_commitments(self, data: dict) -> dict:
        """
        Evaluate commitments against actual financials.
        Returns a dict containing the Credibility Score and evaluated targets list.
        """
        evaluated = []
        scores = []
        
        for c in self.commitments:
            metric = c.get("metric", "")
            target_val = c.get("target_value")
            timeframe = c.get("timeframe", "")
            operator = c.get("operator", "gte")
            promise_text = c.get("promise_text", "")
            unit = c.get("unit", "")
            
            actual = self._get_actual_value(data, metric, timeframe)
            
            status = "PENDING"
            score = None
            
            if actual is not None:
                if operator == "gte":
                    met = actual >= target_val
                    # Proportional score if missed (capped between 0 and 100)
                    if met:
                        score = 100.0
                    else:
                        score = max(0.0, min(100.0, (actual / target_val) * 100.0))
                else:  # lte (e.g. Debt ratio target)
                    met = actual <= target_val
                    if met:
                        score = 100.0
                    else:
                        score = max(0.0, min(100.0, (target_val / actual) * 100.0))
                        
                status = "MET" if met else "MISSED"
                scores.append(score)
            
            evaluated.append({
                "metric": metric,
                "target": target_val,
                "actual": actual,
                "timeframe": timeframe,
                "status": status,
                "score": round(score, 1) if score is not None else None,
                "promise_text": promise_text,
                "unit": unit
            })

        # Calculate credibility score
        if scores:
            credibility_score = sum(scores) / len(scores)
        else:
            # Neutral fallback if no target data is available to evaluate yet
            credibility_score = 75.0

        return {
            "credibility_score": round(credibility_score, 1),
            "evaluated_count": len(scores),
            "total_count": len(self.commitments),
            "commitments": evaluated
        }

if __name__ == "__main__":
    # Standalone sanity check
    tracker = CommitmentTracker("CONFIDENCE PETROLEUM")
    print("Initial commitments loaded:", tracker.commitments)
