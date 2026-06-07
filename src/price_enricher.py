"""
price_enricher.py - Technical Indicators Calculator
====================================================
PURPOSE:
  Takes raw daily candle data (from UpstoxClient or any OHLCV source),
  computes all technical indicators, and returns a dict that plugs
  directly into RuleEngine via `data['price_data']`.

INDICATORS CALCULATED:
  - SMA (50, 150, 200)   : Simple Moving Averages
  - EMA (12, 26)         : Exponential Moving Averages (used in MACD)
  - RSI (14)             : Relative Strength Index (Wilder's smoothing)
  - MACD (12, 26, 9)     : Moving Average Convergence Divergence
  - ATR (14)             : Average True Range
  - VWAP                 : Volume Weighted Average Price (intraday approx)
  - VCP Detection        : Volatility Contraction Pattern (Minervini)
  - Volume vs Average    : Latest volume relative to 20-day average

USAGE:
  from src.price_enricher import PriceEnricher

  candles = [
      {'date': '2025-01-01', 'open': 100, 'high': 105, 'low': 98, 'close': 103, 'volume': 50000},
      ...
  ]
  enricher = PriceEnricher(candles, symbol="HDFCBANK")
  price_data = enricher.enrich_price_data()
  # price_data is ready to be set as data['price_data'] for RuleEngine
"""

import pandas as pd
import numpy as np
from typing import Optional


class PriceEnricher:
    """
    Calculates technical indicators from raw OHLCV candle data.

    Expects a list of dicts with keys: date, open, high, low, close, volume.
    All calculations gracefully handle insufficient data by returning None.
    """

    def __init__(self, candles: list[dict], symbol: str = "UNKNOWN"):
        """
        Initialize with raw candle data.

        Args:
            candles: List of dicts, each with keys:
                     date, open, high, low, close, volume
            symbol: Stock symbol for logging purposes.
        """
        self.symbol = symbol.upper()
        self.df = self._prepare_dataframe(candles)

    def _prepare_dataframe(self, candles: list[dict]) -> pd.DataFrame:
        """
        Convert raw candle dicts to a sorted pandas DataFrame.

        Returns:
            DataFrame with columns: date, open, high, low, close, volume
            sorted by date ascending (oldest first).
        """
        if not candles:
            print(f"[Price Enricher] WARNING: No candle data provided for {self.symbol}")
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(candles)

        # Ensure required columns exist
        required = {"date", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            print(f"[Price Enricher] WARNING: Missing columns {missing} in candle data for {self.symbol}")
            for col in missing:
                df[col] = np.nan

        # Parse dates and sort ascending (oldest first)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        df = df.sort_values("date", ascending=True).reset_index(drop=True)

        # Ensure numeric types for OHLCV
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        print(f"[Price Enricher] Loaded {len(df)} candles for {self.symbol} "
              f"({df['date'].iloc[0].strftime('%Y-%m-%d') if len(df) > 0 else '?'} to "
              f"{df['date'].iloc[-1].strftime('%Y-%m-%d') if len(df) > 0 else '?'})")

        return df

    # -------------------------------------------------------------------------
    # SIMPLE MOVING AVERAGE
    # -------------------------------------------------------------------------

    def calculate_sma(self, period: int) -> pd.Series:
        """
        Calculate Simple Moving Average over the given period.

        Args:
            period: Number of periods for the SMA window.

        Returns:
            pd.Series of SMA values (NaN where insufficient data).
        """
        if len(self.df) < period:
            return pd.Series(dtype=float)
        return self.df["close"].rolling(window=period, min_periods=period).mean()

    # -------------------------------------------------------------------------
    # EXPONENTIAL MOVING AVERAGE
    # -------------------------------------------------------------------------

    def calculate_ema(self, period: int) -> pd.Series:
        """
        Calculate Exponential Moving Average with the given span.

        Uses pandas ewm with span=period (equivalent to alpha = 2/(period+1)).

        Args:
            period: Span for the EMA calculation.

        Returns:
            pd.Series of EMA values.
        """
        if len(self.df) < period:
            return pd.Series(dtype=float)
        return self.df["close"].ewm(span=period, adjust=False).mean()

    # -------------------------------------------------------------------------
    # RELATIVE STRENGTH INDEX (Wilder's Smoothing)
    # -------------------------------------------------------------------------

    def calculate_rsi(self, period: int = 14) -> Optional[float]:
        """
        Calculate RSI using Wilder's smoothing method.

        Formula: RSI = 100 - (100 / (1 + avg_gain / avg_loss))
        Uses exponential smoothing with alpha = 1/period (Wilder's method).

        Args:
            period: RSI lookback period (default 14).

        Returns:
            Latest RSI value as float, or None if insufficient data.
        """
        if len(self.df) < period + 1:
            return None

        delta = self.df["close"].diff()
        gains = delta.where(delta > 0, 0.0)
        losses = (-delta).where(delta < 0, 0.0)

        # Wilder's smoothing: first value is SMA, then exponential with alpha=1/period
        avg_gain = gains.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        avg_loss = losses.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))

        latest = rsi.iloc[-1]
        if pd.isna(latest):
            return None
        return round(float(latest), 2)

    # -------------------------------------------------------------------------
    # MACD (Moving Average Convergence Divergence)
    # -------------------------------------------------------------------------

    def calculate_macd(self, fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict]:
        """
        Calculate MACD indicator.

        MACD Line   = EMA(fast) - EMA(slow)
        Signal Line = EMA(signal) of MACD Line
        Histogram   = MACD Line - Signal Line

        Trend is determined by:
        - 'bullish': MACD > Signal AND histogram > 0
        - 'bearish': MACD < Signal AND histogram < 0

        Args:
            fast:   Fast EMA period (default 12).
            slow:   Slow EMA period (default 26).
            signal: Signal line EMA period (default 9).

        Returns:
            Dict with keys: macd, signal, histogram, trend.
            Returns None if insufficient data.
        """
        # Need at least slow + signal periods of data for meaningful MACD
        if len(self.df) < slow + signal:
            return None

        ema_fast = self.df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = self.df["close"].ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line

        macd_val = macd_line.iloc[-1]
        signal_val = signal_line.iloc[-1]
        hist_val = histogram.iloc[-1]

        if any(pd.isna(v) for v in [macd_val, signal_val, hist_val]):
            return None

        # Determine trend based on current values
        if macd_val > signal_val and hist_val > 0:
            trend = "bullish"
        elif macd_val < signal_val and hist_val < 0:
            trend = "bearish"
        else:
            trend = "neutral"

        return {
            "macd": round(float(macd_val), 4),
            "signal": round(float(signal_val), 4),
            "histogram": round(float(hist_val), 4),
            "trend": trend,
        }

    # -------------------------------------------------------------------------
    # AVERAGE TRUE RANGE
    # -------------------------------------------------------------------------

    def calculate_atr(self, period: int = 14) -> Optional[float]:
        """
        Calculate Average True Range.

        True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
        ATR = Simple moving average of True Range over the period.

        Args:
            period: ATR lookback period (default 14).

        Returns:
            Latest ATR value as float, or None if insufficient data.
        """
        if len(self.df) < period + 1:
            return None

        high = self.df["high"]
        low = self.df["low"]
        prev_close = self.df["close"].shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()

        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = true_range.rolling(window=period, min_periods=period).mean()

        latest = atr.iloc[-1]
        if pd.isna(latest):
            return None
        return round(float(latest), 4)

    # -------------------------------------------------------------------------
    # VWAP (Volume Weighted Average Price)
    # -------------------------------------------------------------------------

    def calculate_vwap(self) -> Optional[float]:
        """
        Calculate VWAP using the most recent trading day's data.

        For daily candle data, VWAP is approximated as the typical price
        weighted by volume over the last trading session.
        Typical Price = (High + Low + Close) / 3

        Returns:
            VWAP value as float, or None if no data.
        """
        if len(self.df) == 0:
            return None

        # For daily data, compute a running VWAP over the full period
        # (for intraday you'd reset daily, but with daily candles this
        # gives a cumulative VWAP which is useful as a reference line)
        typical_price = (self.df["high"] + self.df["low"] + self.df["close"]) / 3.0
        volume = self.df["volume"]

        # Avoid division by zero
        cumulative_vol = volume.cumsum()
        if cumulative_vol.iloc[-1] == 0:
            return None

        cumulative_tp_vol = (typical_price * volume).cumsum()
        vwap = cumulative_tp_vol / cumulative_vol

        latest = vwap.iloc[-1]
        if pd.isna(latest):
            return None
        return round(float(latest), 4)

    # -------------------------------------------------------------------------
    # VCP DETECTION (Volatility Contraction Pattern)
    # -------------------------------------------------------------------------

    def detect_vcp(self) -> dict:
        """
        Detect Volatility Contraction Pattern (Minervini).

        Looks for 3+ successive contractions in weekly price range where
        each contraction is at least 25% tighter than the previous one.
        Analyzes over 4-8 week rolling windows.

        Returns:
            Dict with keys:
                detected  (bool)  : Whether a VCP pattern was found.
                contractions (int): Number of successive contractions found.
                tightness (float) : Ratio of last contraction to first (lower = tighter).
        """
        default = {"detected": False, "contractions": 0, "tightness": 0.0}

        # Need at least 8 weeks (40 trading days) of data
        min_days = 40
        if len(self.df) < min_days:
            return default

        # Resample to weekly ranges (Mon-Fri)
        df_recent = self.df.tail(60).copy()  # Look at last ~12 weeks
        df_recent = df_recent.set_index("date")

        try:
            weekly_high = df_recent["high"].resample("W").max()
            weekly_low = df_recent["low"].resample("W").min()
        except Exception:
            return default

        # Calculate weekly ranges (high - low spread)
        weekly_range = weekly_high - weekly_low
        weekly_range = weekly_range.dropna()

        if len(weekly_range) < 4:
            return default

        # Detect successive contractions: each range should be smaller
        # than the previous by at least 25%
        contractions = 0
        ranges = weekly_range.values.tolist()

        for i in range(1, len(ranges)):
            if ranges[i - 1] > 0 and ranges[i] < ranges[i - 1] * 0.75:
                contractions += 1
            else:
                # Reset if a range expands (non-contraction)
                if contractions < 3:
                    contractions = 0

        # Tightness: ratio of last range to the max range in the window
        max_range = max(ranges) if ranges else 0
        last_range = ranges[-1] if ranges else 0
        tightness = round(last_range / max_range, 4) if max_range > 0 else 0.0

        detected = contractions >= 3

        return {
            "detected": detected,
            "contractions": contractions,
            "tightness": tightness,
        }

    # -------------------------------------------------------------------------
    # VOLUME VS AVERAGE
    # -------------------------------------------------------------------------

    def get_volume_vs_avg(self, period: int = 20) -> Optional[float]:
        """
        Calculate latest volume as percentage above/below the moving average.

        Formula: (latest_volume / SMA(volume, period) - 1) * 100

        Args:
            period: Number of days for the volume moving average (default 20).

        Returns:
            Percentage difference (positive = above avg, negative = below).
            Returns None if insufficient data.
        """
        if len(self.df) < period:
            return None

        volume = self.df["volume"]
        avg_volume = volume.rolling(window=period, min_periods=period).mean().iloc[-1]

        if pd.isna(avg_volume) or avg_volume == 0:
            return None

        latest_volume = volume.iloc[-1]
        if pd.isna(latest_volume):
            return None

        pct = (float(latest_volume) / float(avg_volume) - 1.0) * 100.0
        return round(pct, 2)

    # -------------------------------------------------------------------------
    # MACD SIGNAL STRING (for rule_engine.py)
    # -------------------------------------------------------------------------

    def _get_macd_signal_string(self) -> str:
        """
        Determine MACD signal as a human-readable string for rule_engine.py.

        Checks the last two MACD/Signal values to detect crossovers:
        - 'bullish_crossover'  : MACD crossed above Signal line
        - 'bearish_crossover'  : MACD crossed below Signal line
        - 'neutral'            : No crossover detected

        Returns:
            One of: 'bullish_crossover', 'bearish_crossover', 'neutral'
        """
        if len(self.df) < 35:  # Need enough for MACD(26) + Signal(9)
            return "neutral"

        ema_fast = self.df["close"].ewm(span=12, adjust=False).mean()
        ema_slow = self.df["close"].ewm(span=26, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=9, adjust=False).mean()

        if len(macd_line) < 2:
            return "neutral"

        # Check for crossover in the most recent bar
        macd_prev = macd_line.iloc[-2]
        macd_curr = macd_line.iloc[-1]
        sig_prev = signal_line.iloc[-2]
        sig_curr = signal_line.iloc[-1]

        if any(pd.isna(v) for v in [macd_prev, macd_curr, sig_prev, sig_curr]):
            return "neutral"

        # Bullish crossover: MACD was below signal, now above
        if macd_prev <= sig_prev and macd_curr > sig_curr:
            return "bullish_crossover"

        # Bearish crossover: MACD was above signal, now below
        if macd_prev >= sig_prev and macd_curr < sig_curr:
            return "bearish_crossover"

        return "neutral"

    # -------------------------------------------------------------------------
    # ENRICHED PRICE DATA (main output for rule_engine.py)
    # -------------------------------------------------------------------------

    def enrich_price_data(self) -> dict:
        """
        Calculate all indicators and return a dict matching rule_engine.py expectations.

        The returned dict maps directly to `data['price_data']` consumed by
        RuleEngine._evaluate_trend_template() and _evaluate_technicals().

        Returns:
            Dict with keys:
                current_price    (float|None) : Latest closing price
                52w_high         (float|None) : 52-week high
                52w_low          (float|None) : 52-week low
                dma_50           (float|None) : 50-day SMA
                dma_150          (float|None) : 150-day SMA
                dma_200          (float|None) : 200-day SMA
                rsi              (float|None) : 14-period RSI
                volume_vs_avg_pct(float|None) : Volume vs 20-day average %
                macd_signal      (str)        : 'bullish_crossover', 'bearish_crossover', 'neutral'
                atr              (float|None) : 14-period ATR
                vcp_detected     (bool)       : VCP pattern detected
        """
        print(f"[Price Enricher] Calculating indicators for {self.symbol}...")

        if len(self.df) == 0:
            print(f"[Price Enricher] No data available for {self.symbol}. Returning empty price data.")
            return {
                "current_price": None,
                "52w_high": None,
                "52w_low": None,
                "dma_50": None,
                "dma_150": None,
                "dma_200": None,
                "rsi": None,
                "volume_vs_avg_pct": None,
                "macd_signal": "neutral",
                "atr": None,
                "vcp_detected": False,
            }

        # Current price = latest close
        current_price = float(self.df["close"].iloc[-1]) if not pd.isna(self.df["close"].iloc[-1]) else None

        # 52-week high/low (approximately 252 trading days)
        trading_days_52w = min(252, len(self.df))
        recent_df = self.df.tail(trading_days_52w)
        high_52w = float(recent_df["high"].max()) if not recent_df["high"].isna().all() else None
        low_52w = float(recent_df["low"].min()) if not recent_df["low"].isna().all() else None

        # SMAs: return latest value or None if not enough data
        def _latest_sma(period: int) -> Optional[float]:
            sma = self.calculate_sma(period)
            if sma.empty:
                return None
            val = sma.iloc[-1]
            return round(float(val), 4) if not pd.isna(val) else None

        dma_50 = _latest_sma(50)
        dma_150 = _latest_sma(150)
        dma_200 = _latest_sma(200)

        # RSI
        rsi = self.calculate_rsi(14)

        # Volume vs average
        volume_vs_avg = self.get_volume_vs_avg(20)

        # MACD signal string for rule_engine.py
        macd_signal = self._get_macd_signal_string()

        # ATR
        atr = self.calculate_atr(14)

        # VCP detection
        vcp = self.detect_vcp()

        result = {
            "current_price": round(current_price, 4) if current_price is not None else None,
            "52w_high": round(high_52w, 4) if high_52w is not None else None,
            "52w_low": round(low_52w, 4) if low_52w is not None else None,
            "dma_50": dma_50,
            "dma_150": dma_150,
            "dma_200": dma_200,
            "rsi": rsi,
            "volume_vs_avg_pct": volume_vs_avg,
            "macd_signal": macd_signal,
            "atr": atr,
            "vcp_detected": vcp["detected"],
        }

        # Log what was computed vs what had insufficient data
        computed = [k for k, v in result.items() if v is not None and v is not False and v != "neutral"]
        skipped = [k for k, v in result.items()
                   if v is None and k not in ("macd_signal", "vcp_detected")]
        if skipped:
            print(f"[Price Enricher] Insufficient data for: {', '.join(skipped)} "
                  f"(have {len(self.df)} candles)")
        print(f"[Price Enricher] Computed {len(computed)} indicators for {self.symbol}")

        return result


# ── Standalone Test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    """Quick sanity check with synthetic data."""
    import random

    print("=" * 60)
    print("  PriceEnricher - Standalone Test")
    print("=" * 60)

    # Generate 250 days of synthetic candle data
    base_price = 1000.0
    candles = []
    for i in range(250):
        date = pd.Timestamp("2025-01-01") + pd.Timedelta(days=i)
        # Random walk with slight upward bias
        base_price *= 1 + random.uniform(-0.02, 0.025)
        o = round(base_price * random.uniform(0.99, 1.01), 2)
        h = round(max(o, base_price) * random.uniform(1.0, 1.03), 2)
        l = round(min(o, base_price) * random.uniform(0.97, 1.0), 2)
        c = round(base_price, 2)
        v = random.randint(100000, 500000)
        candles.append({"date": date.strftime("%Y-%m-%d"), "open": o, "high": h, "low": l, "close": c, "volume": v})

    enricher = PriceEnricher(candles, symbol="TEST")
    result = enricher.enrich_price_data()

    print("\n--- Enriched Price Data ---")
    for key, value in result.items():
        print(f"  {key:>20s}: {value}")

    # Also test individual methods
    print("\n--- Individual Indicator Tests ---")
    macd = enricher.calculate_macd()
    print(f"  MACD: {macd}")
    vcp = enricher.detect_vcp()
    print(f"  VCP:  {vcp}")
    vwap = enricher.calculate_vwap()
    print(f"  VWAP: {vwap}")

    print("\n[Test] All indicators computed successfully!")
