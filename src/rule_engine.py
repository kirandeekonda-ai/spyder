"""
rule_engine.py - The Math Brain of the Hybrid Stock Analyst
============================================================
PURPOSE:
  Reads scraped stock data (JSON), applies all rules from config/rules.json,
  calculates a composite score (0-100), and produces a structured verdict.

HOW SCORING WORKS:
  Total score = Trend Template Score (35%) + Fundamentals Score (35%) + Technicals Score (30%)

  BUY   : Score >= 70
  HOLD  : Score >= 45
  SELL  : Score < 45

USAGE:
  from src.rule_engine import RuleEngine
  engine = RuleEngine("HDFCBANK", data_dict)
  result = engine.evaluate()
  engine.print_report()
"""

import json
import os
import sys
import re
from pathlib import Path
from datetime import datetime
from typing import Any

# -- Paths --------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.parent
RULES_FILE = BASE_DIR / "config" / "rules.json"
MEMORY_DIR = BASE_DIR / "memory" / "stocks"


class RuleEngine:
    def __init__(self, symbol: str, data: dict):
        self.symbol = symbol.upper()
        self.data = data
        self.rules = self._load_rules()
        self.result = {
            "symbol": self.symbol,
            "evaluated_at": datetime.now().isoformat(),
            "score": 0,
            "verdict": "INCOMPLETE",
            "trend_score": 0,
            "fundamental_score": 0,
            "technical_score": 0,
            "checks": {},
            "strengths": [],
            "weaknesses": [],
            "risk_flags": [],
            "stop_loss": None,
            "target": None,
            "notes": ""
        }

    def _load_rules(self) -> dict:
        with open(RULES_FILE, "r") as f:
            return json.load(f)

    def _get(self, *keys, default=None) -> Any:
        """Safe nested dict getter for scraped data."""
        val = self.data
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k, default)
            else:
                return default
        return val if val is not None else default

    def _parse_num(self, val) -> float:
        """Parse a value that might be a string like '12.5%' or '1,234'."""
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        cleaned = re.sub(r'[^0-9.\-]', '', str(val)).strip()
        try:
            return float(cleaned)
        except ValueError:
            return None

    # -------------------------------------------------------------------------
    # TREND TEMPLATE EVALUATION (35% of total score)
    # -------------------------------------------------------------------------

    def _evaluate_trend_template(self) -> float:
        """
        Minervini's Trend Template: 8 conditions.
        All must be TRUE for a perfect trend score.
        Each satisfied condition = 12.5 points (8 x 12.5 = 100).
        Weight: 35% of total score.
        """
        checks = {}
        price = self._parse_num(self._get("price_data", "current_price"))
        high_52w = self._parse_num(self._get("price_data", "52w_high"))
        low_52w = self._parse_num(self._get("price_data", "52w_low"))
        dma_50 = self._parse_num(self._get("price_data", "dma_50"))
        dma_150 = self._parse_num(self._get("price_data", "dma_150"))
        dma_200 = self._parse_num(self._get("price_data", "dma_200"))

        score = 0
        max_score = 8

        # Condition 1: Price > 150 DMA
        c1 = bool(price and dma_150 and price > dma_150)
        checks["price_above_150dma"] = {"pass": c1, "price": price, "dma_150": dma_150}
        if c1: score += 1

        # Condition 2: Price > 200 DMA
        c2 = bool(price and dma_200 and price > dma_200)
        checks["price_above_200dma"] = {"pass": c2, "price": price, "dma_200": dma_200}
        if c2: score += 1

        # Condition 3: 150 DMA > 200 DMA
        c3 = bool(dma_150 and dma_200 and dma_150 > dma_200)
        checks["dma_150_above_200"] = {"pass": c3, "dma_150": dma_150, "dma_200": dma_200}
        if c3: score += 1

        # Condition 4: 50 DMA > 150 DMA and 200 DMA
        c4 = bool(dma_50 and dma_150 and dma_200 and dma_50 > dma_150 and dma_50 > dma_200)
        checks["dma_50_above_150_200"] = {"pass": c4, "dma_50": dma_50}
        if c4: score += 1

        # Condition 5: Price > 50 DMA
        c5 = bool(price and dma_50 and price > dma_50)
        checks["price_above_50dma"] = {"pass": c5, "price": price, "dma_50": dma_50}
        if c5: score += 1

        # Condition 6: Price at least 25% above 52-week low
        if price and low_52w and low_52w > 0:
            pct_above_low = (price - low_52w) / low_52w * 100
            c6 = pct_above_low >= 25
            checks["price_25pct_above_52w_low"] = {"pass": c6, "pct_above_low": round(pct_above_low, 1)}
        else:
            c6 = False
            checks["price_25pct_above_52w_low"] = {"pass": False, "pct_above_low": None}
        if c6: score += 1

        # Condition 7: Price within 25% of 52-week high
        if price and high_52w and high_52w > 0:
            pct_from_high = (high_52w - price) / high_52w * 100
            c7 = pct_from_high <= 25
            checks["price_within_25pct_of_52w_high"] = {"pass": c7, "pct_from_high": round(pct_from_high, 1)}
        else:
            c7 = False
            checks["price_within_25pct_of_52w_high"] = {"pass": False, "pct_from_high": None}
        if c7: score += 1

        # Condition 8: RS Rating
        rs = self._parse_num(self._get("ratios", "rs_rating"))
        rs_min = self.rules["trend_template"]["rs_rating_min"]
        c8 = bool(rs and rs >= rs_min)
        checks["rs_rating"] = {"pass": c8, "rs": rs, "min_required": rs_min}
        if c8: score += 1

        # If DMA data not available, give partial neutral score
        missing_data = not any([dma_50, dma_150, dma_200])
        if missing_data:
            checks["_note"] = "DMA data not available. Enrich with price data source."
            trend_score = 50.0
        else:
            trend_score = (score / max_score) * 100

        self.result["checks"]["trend_template"] = checks
        self.result["checks"]["trend_template"]["conditions_met"] = f"{score}/{max_score}"
        return trend_score

    # -------------------------------------------------------------------------
    # FUNDAMENTALS EVALUATION (35% of total score)
    # -------------------------------------------------------------------------

    def _evaluate_fundamentals(self) -> float:
        checks = {}
        rules = self.rules["fundamentals"]
        score = 0
        max_score = 0

        def check(name: str, passed: bool, detail: dict = None, weight: int = 1):
            nonlocal score, max_score
            checks[name] = {"pass": passed}
            if detail:
                checks[name].update(detail)
            max_score += weight
            if passed:
                score += weight
                self.result["strengths"].append(name.replace("_", " ").title())
            else:
                self.result["weaknesses"].append(name.replace("_", " ").title())

        ratios = self.data.get("ratios", {})
        sh = self.data.get("shareholding", {})

        # ROE
        roe = self._parse_num(ratios.get("roe"))
        check("roe_above_17pct", bool(roe and roe >= rules["roe"]["min_pct"]),
              {"roe": roe, "required": rules["roe"]["min_pct"]}, weight=2)

        # ROCE
        roce = self._parse_num(ratios.get("roce"))
        check("roce_above_15pct", bool(roce and roce >= rules["roce"]["min_pct"]),
              {"roce": roce, "required": rules["roce"]["min_pct"]}, weight=2)

        # Debt to Equity
        de = self._parse_num(ratios.get("debt_to_equity"))
        check("debt_to_equity_below_1", bool(de is not None and de <= rules["debt_to_equity"]["max"]),
              {"de_ratio": de, "max_allowed": rules["debt_to_equity"]["max"]}, weight=2)

        # Promoter Holding
        promoter = self._parse_num(sh.get("promoter"))
        check("promoter_holding_above_40pct",
              bool(promoter and promoter >= rules["promoter_holding"]["min_pct"]),
              {"promoter_pct": promoter, "required": rules["promoter_holding"]["min_pct"]}, weight=2)

        # FII/DII Presence
        fii = self._parse_num(sh.get("fii"))
        dii = self._parse_num(sh.get("dii"))
        institutional_present = bool((fii and fii > 1) or (dii and dii > 1))
        check("institutional_holding_present", institutional_present,
              {"fii": fii, "dii": dii}, weight=1)

        # EPS / Profit Growth
        profit_growth = self._parse_num(ratios.get("profit_growth"))
        check("eps_growth_above_25pct",
              bool(profit_growth and profit_growth >= rules["eps_growth"]["annual_min_pct"]),
              {"profit_growth_pct": profit_growth, "required": rules["eps_growth"]["annual_min_pct"]}, weight=3)

        # Sales Growth
        sales_growth = self._parse_num(ratios.get("sales_growth"))
        check("sales_growth_above_15pct",
              bool(sales_growth and sales_growth >= rules["sales_growth"]["annual_min_pct"]),
              {"sales_growth_pct": sales_growth, "required": rules["sales_growth"]["annual_min_pct"]}, weight=2)

        # Management Credibility Score
        try:
            from commitment_tracker import CommitmentTracker
            tracker = CommitmentTracker(self.symbol)
            eval_result = tracker.evaluate_commitments(self.data)
            cred_score = eval_result["credibility_score"]
            self.result["management_credibility"] = eval_result
            
            min_cred = rules.get("management_credibility", {}).get("min_score", 70.0)
            check("management_credibility_sufficient", bool(cred_score >= min_cred),
                  {"credibility_score": cred_score, "required": min_cred}, weight=2)
        except Exception as e:
            print(f"[RuleEngine] [WARN] Failed to evaluate management credibility: {e}")

        # PE vs Sector (informational)
        pe = self._parse_num(ratios.get("p_e"))
        if pe:
            checks["pe_ratio"] = {"value": pe, "note": "Compare with sector peers manually"}

        fundamental_score = (score / max_score * 100) if max_score > 0 else 0
        self.result["checks"]["fundamentals"] = checks
        self.result["checks"]["fundamentals"]["score_raw"] = f"{score}/{max_score}"
        return fundamental_score

    # -------------------------------------------------------------------------
    # TECHNICALS EVALUATION (30% of total score)
    # -------------------------------------------------------------------------

    def _evaluate_technicals(self) -> float:
        checks = {}
        rules = self.rules["technicals"]
        score = 0
        max_score = 0

        def check(name: str, passed: bool, detail: dict = None, weight: int = 1):
            nonlocal score, max_score
            checks[name] = {"pass": passed}
            if detail:
                checks[name].update(detail)
            max_score += weight
            if passed:
                score += weight

        price_data = self.data.get("price_data", {})
        rsi = self._parse_num(price_data.get("rsi"))
        volume_vs_avg = self._parse_num(price_data.get("volume_vs_avg_pct"))
        macd_signal = str(price_data.get("macd_signal", "")).lower()

        # RSI in buy zone (50-80)
        rsi_min = rules["rsi"]["buy_zone_min"]
        rsi_max = rules["rsi"]["buy_zone_max"]
        rsi_ok = bool(rsi and rsi_min <= rsi <= rsi_max)
        check("rsi_in_buy_zone", rsi_ok,
              {"rsi": rsi, "ideal_range": f"{rsi_min}-{rsi_max}"}, weight=2)

        if rsi and rsi > rules["rsi"]["overbought_max"]:
            self.result["risk_flags"].append(
                f"RSI {rsi} is OVERBOUGHT (>{rules['rsi']['overbought_max']}). Do not chase."
            )

        # Volume on breakout
        vol_min = rules["volume"]["breakout_volume_min_pct_above_avg"]
        vol_ok = bool(volume_vs_avg and volume_vs_avg >= vol_min)
        check("breakout_volume_sufficient", vol_ok,
              {"volume_vs_avg_pct": volume_vs_avg, "required_pct": vol_min}, weight=2)

        # MACD bullish
        macd_ok = "bullish" in macd_signal or "crossover" in macd_signal
        check("macd_bullish_signal", macd_ok,
              {"macd_signal": price_data.get("macd_signal", "N/A")}, weight=2)

        # Relative Strength Line vs Nifty 50
        rs_pct = self._parse_num(price_data.get("rs_ratio_pct_below_52w_high"))
        rs_trending = price_data.get("rs_line_trending_up", False)
        rs_max_pct = rules.get("relative_strength", {}).get("rs_ratio_max_pct_below_52week_high", 15.0)
        
        if rs_pct is not None:
            rs_ok = bool(rs_pct <= rs_max_pct)
            check("rs_line_outperforming_nifty", rs_ok,
                  {"rs_pct_below_52w_high": rs_pct, "max_allowed": rs_max_pct, "trending_up": rs_trending}, weight=2)
            
            # Extreme strength notification
            if rs_pct <= 5.0 and rs_trending:
                self.result["strengths"].append("Extreme Relative Strength (Near 52W High)")

        # If technical data not available, give neutral score
        if not any([rsi, volume_vs_avg]):
            checks["_note"] = "Technical indicators not available. Enrich with price data."
            technical_score = 50.0
        else:
            technical_score = (score / max_score * 100) if max_score > 0 else 0

        self.result["checks"]["technicals"] = checks
        return technical_score

    # -------------------------------------------------------------------------
    # MACRO FILTERS
    # -------------------------------------------------------------------------

    def _evaluate_macro(self):
        """Check macro risk flags. These don't affect score but add warnings."""
        rules = self.rules["macro_filters"]
        india_vix = self._parse_num(self.data.get("macro", {}).get("india_vix"))

        if india_vix:
            max_vix = rules["india_vix_max"]
            if india_vix > 30:
                self.result["risk_flags"].append(
                    f"[CRITICAL] INDIA VIX = {india_vix} (>30). Market extremely fearful. NO NEW POSITIONS."
                )
            elif india_vix > max_vix:
                self.result["risk_flags"].append(
                    f"[WARNING] INDIA VIX = {india_vix} (>{max_vix}). Reduce position size by 50%."
                )

    # -------------------------------------------------------------------------
    # POSITION SIZING CALCULATOR
    # -------------------------------------------------------------------------

    def calculate_position(self, portfolio_value: float, entry_price: float,
                           stop_loss_pct: float = None) -> dict:
        """
        Risk-based position sizing.
        Risk per trade = 1.5% of portfolio value.
        Stop loss = 7% below entry (default from rules).
        """
        if stop_loss_pct is None:
            stop_loss_pct = self.rules["sell_rules"]["stop_loss_hard_pct"]

        max_risk = portfolio_value * (self.rules["position_sizing"]["max_risk_per_trade_pct"] / 100)
        sl_price = entry_price * (1 - stop_loss_pct / 100)
        risk_per_share = entry_price - sl_price
        shares = int(max_risk / risk_per_share)
        pos_value = shares * entry_price
        pos_pct = (pos_value / portfolio_value) * 100
        max_pct = self.rules["position_sizing"]["max_portfolio_in_single_stock_pct"]

        if pos_pct > max_pct:
            shares = int((portfolio_value * max_pct / 100) / entry_price)
            pos_value = shares * entry_price
            pos_pct = max_pct

        return {
            "entry_price": entry_price,
            "stop_loss_price": round(sl_price, 2),
            "stop_loss_pct": stop_loss_pct,
            "shares_to_buy": shares,
            "position_value_INR": round(pos_value, 2),
            "position_pct_of_portfolio": round(pos_pct, 2),
            "max_risk_amount_INR": round(max_risk, 2),
            "target_price": round(entry_price * 1.21, 2),
            "risk_reward_ratio": "1:3 (approximate)"
        }

    # -------------------------------------------------------------------------
    # MAIN EVALUATE METHOD
    # -------------------------------------------------------------------------

    def evaluate(self) -> dict:
        """Run all evaluations and produce final verdict."""
        trend_score = self._evaluate_trend_template()
        fundamental_score = self._evaluate_fundamentals()
        technical_score = self._evaluate_technicals()
        self._evaluate_macro()

        w_trend = self.rules["trend_template"]["_weight_in_total_score"] / 100
        w_fund = self.rules["fundamentals"]["_weight_in_total_score"] / 100
        w_tech = self.rules["technicals"]["_weight_in_total_score"] / 100

        composite = (trend_score * w_trend) + (fundamental_score * w_fund) + (technical_score * w_tech)

        self.result["trend_score"] = round(trend_score, 1)
        self.result["fundamental_score"] = round(fundamental_score, 1)
        self.result["technical_score"] = round(technical_score, 1)
        self.result["score"] = round(composite, 1)

        thresholds = self.rules["verdict_thresholds"]
        if composite >= thresholds["BUY"]:
            self.result["verdict"] = "[BUY]"
        elif composite >= thresholds["HOLD"]:
            self.result["verdict"] = "[HOLD/WATCH]"
        else:
            self.result["verdict"] = "[SELL/AVOID]"

        return self.result

    # -------------------------------------------------------------------------
    # REPORT PRINTER
    # -------------------------------------------------------------------------

    def print_report(self):
        """Print a clean ASCII analysis report to console."""
        r = self.result
        sep = "=" * 60

        print(f"\n{sep}")
        print(f"  HYBRID STOCK ANALYST -- {r['symbol']}")
        print(f"  Evaluated: {r['evaluated_at'][:19]}")
        print(sep)
        print(f"  VERDICT  : {r['verdict']}")
        print(f"  SCORE    : {r['score']} / 100")
        print(f"  |-- Trend Template  : {r['trend_score']}/100  (35% weight)")
        print(f"  |-- Fundamentals    : {r['fundamental_score']}/100  (35% weight)")
        print(f"  `-- Technicals      : {r['technical_score']}/100  (30% weight)")
        print(sep)

        if r["strengths"]:
            print("\n  [+] STRENGTHS:")
            for s in r["strengths"]:
                print(f"       * {s}")

        if r["weaknesses"]:
            print("\n  [-] WEAKNESSES:")
            for w in r["weaknesses"]:
                print(f"       * {w}")

        if r["risk_flags"]:
            print("\n  [!] RISK FLAGS:")
            for rf in r["risk_flags"]:
                print(f"       * {rf}")

        print(f"\n{sep}\n")

    def save_memory(self) -> Path:
        """Save analysis result to memory/stocks/<SYMBOL>.json"""
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        mem_file = MEMORY_DIR / f"{self.symbol}.json"

        history = []
        if mem_file.exists():
            with open(mem_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
                history = existing.get("history", [])

        history.append({
            "date": self.result["evaluated_at"],
            "score": self.result["score"],
            "verdict": self.result["verdict"],
            "trend_score": self.result["trend_score"],
            "fundamental_score": self.result["fundamental_score"],
            "technical_score": self.result["technical_score"]
        })

        output = {
            "symbol": self.symbol,
            "last_analysis": self.result,
            "history": history
        }

        with open(mem_file, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        print(f"[Memory] Analysis saved to: {mem_file}")
        return mem_file
