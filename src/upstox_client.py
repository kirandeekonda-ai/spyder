"""
upstox_client.py - Upstox API v2 Data Client
=============================================
PURPOSE:
  Fetches market data from the Upstox API:
  - Instrument resolution (symbol -> instrument_key)
  - Historical candles (daily and weekly OHLCV)
  - Live quotes (LTP and full market quote)

  Includes automatic retries, rate-limit awareness, and local
  instrument caching to minimize API calls.

API LIMITS:
  - 50 requests/second, 500/minute, 2000/30 minutes
  - Daily candle history: up to 1 year
  - Weekly candle history: up to 10 years

USAGE:
  from src.upstox_auth import UpstoxAuth
  from src.upstox_client import UpstoxClient

  auth = UpstoxAuth()
  client = UpstoxClient(auth)
  key = client.resolve_instrument_key("HDFCBANK")
  candles = client.fetch_daily_candles(key, days=365)
  ltp = client.fetch_ltp(key)
"""

import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

from upstox_auth import UpstoxAuth

# ── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent
INSTRUMENT_CACHE_FILE = BASE_DIR / "sessions" / "instrument_cache.json"

# ── Constants ────────────────────────────────────────────────────────────────

UPSTOX_BASE = "https://api.upstox.com"

MAX_RETRIES = 3
RETRY_DELAY_SECS = 2       # Base delay between retries (doubles each retry)
RATE_LIMIT_PAUSE = 1.5     # Pause when rate-limited (429)


# ── Client Class ─────────────────────────────────────────────────────────────

class UpstoxClient:
    def __init__(self, auth: UpstoxAuth):
        self.auth = auth
        self._instrument_cache: dict = self._load_instrument_cache()

    # ── Instrument Cache ─────────────────────────────────────────────────────

    def _load_instrument_cache(self) -> dict:
        """Load the local instrument key cache from sessions/instrument_cache.json."""
        if INSTRUMENT_CACHE_FILE.exists():
            try:
                with open(INSTRUMENT_CACHE_FILE, "r") as f:
                    cache = json.load(f)
                print(f"[Upstox] Loaded instrument cache ({len(cache)} symbols)")
                return cache
            except (json.JSONDecodeError, IOError):
                pass
        return {}

    def _save_instrument_cache(self):
        """Persist the instrument cache to disk."""
        INSTRUMENT_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(INSTRUMENT_CACHE_FILE, "w") as f:
            json.dump(self._instrument_cache, f, indent=2)

    # ── HTTP Helper ──────────────────────────────────────────────────────────

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        Make an authenticated HTTP request with retry logic.

        Handles:
        - Automatic auth header injection
        - Retries on 5xx errors and connection failures
        - Rate-limit backoff on 429 responses
        """
        headers = self.auth.get_headers()
        kwargs.setdefault("headers", {}).update(headers)
        kwargs.setdefault("timeout", 30)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.request(method, url, **kwargs)

                if resp.status_code == 429:
                    # Rate limited — pause and retry
                    wait = RATE_LIMIT_PAUSE * attempt
                    print(f"[Upstox] Rate limited (429). Waiting {wait:.1f}s... (attempt {attempt}/{MAX_RETRIES})")
                    time.sleep(wait)
                    continue

                if resp.status_code >= 500:
                    # Server error — retry with backoff
                    wait = RETRY_DELAY_SECS * attempt
                    print(f"[Upstox] Server error ({resp.status_code}). Retrying in {wait}s... (attempt {attempt}/{MAX_RETRIES})")
                    time.sleep(wait)
                    continue

                # Success or client error (4xx) — return immediately
                return resp

            except requests.exceptions.ConnectionError as e:
                wait = RETRY_DELAY_SECS * attempt
                print(f"[Upstox] Connection error: {e}. Retrying in {wait}s... (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(wait)

            except requests.exceptions.Timeout:
                wait = RETRY_DELAY_SECS * attempt
                print(f"[Upstox] Request timed out. Retrying in {wait}s... (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(wait)

        # All retries exhausted — make one final attempt and let it raise
        return requests.request(method, url, **kwargs)

    # ── Instrument Resolution ────────────────────────────────────────────────

    def resolve_instrument_key(self, symbol: str) -> str:
        """
        Resolve a trading symbol (e.g. 'HDFCBANK') to its Upstox instrument key
        (e.g. 'NSE_EQ|INE040A01034').

        Checks the local cache first, then falls back to the instrument search API.

        Args:
            symbol: NSE trading symbol (case-insensitive).

        Returns:
            The instrument key string (e.g. 'NSE_EQ|INE040A01034').

        Raises:
            ValueError: If the symbol cannot be resolved.
        """
        symbol = symbol.upper().strip()

        # Check cache first
        if symbol in self._instrument_cache:
            cached = self._instrument_cache[symbol]
            print(f"[Upstox] Instrument key for {symbol}: {cached} (cached)")
            return cached

        # Search via API
        print(f"[Upstox] Searching instrument key for {symbol}...")
        url = f"{UPSTOX_BASE}/v2/instruments/search"
        params = {
            "query": symbol,
            "exchanges": "NSE",
            "segments": "EQ",
        }

        resp = self._request("GET", url, params=params)

        if resp.status_code != 200:
            raise ValueError(
                f"[Upstox] Instrument search failed for '{symbol}' "
                f"(HTTP {resp.status_code}): {resp.text}"
            )

        data = resp.json()
        instruments = data.get("data", [])

        # Filter for equity segment
        eq_instruments = [inst for inst in instruments if inst.get("instrument_type") == "EQ"]

        if not eq_instruments:
            raise ValueError(
                f"[Upstox] No equity instrument found for '{symbol}' in search results."
            )

        best_match = None
        best_score = -1

        for inst in eq_instruments:
            trading_symbol = inst.get("trading_symbol", "").upper()
            name = inst.get("name", "").upper()
            score = 0

            # 1. Exact match on trading symbol
            if trading_symbol == symbol:
                score = 100
            # 2. Query matches trading symbol prefix/suffix
            elif trading_symbol in symbol or symbol in trading_symbol:
                score = 80
            # 3. Exact name match
            elif name == symbol:
                score = 70
            # 4. Partial match on name
            elif symbol in name or name in symbol:
                score = 60
            # 5. First word of query matches name or trading symbol
            else:
                words = symbol.split()
                if words and (words[0] in name or words[0] in trading_symbol):
                    score = 50
                # 6. Single result fallback
                elif len(eq_instruments) == 1:
                    score = 30

            if score > best_score:
                best_score = score
                best_match = inst

        if not best_match or best_score <= 0:
            raise ValueError(
                f"[Upstox] No suitable equity instrument found for '{symbol}'. "
                f"API returned {len(instruments)} results, none matched our scoring thresholds. "
                f"Raw search results: {[i.get('trading_symbol') + ':' + i.get('name', '') for i in instruments]}"
            )

        instrument_key = best_match["instrument_key"]

        # Cache the result
        self._instrument_cache[symbol] = instrument_key
        self._save_instrument_cache()
        print(f"[Upstox] Instrument key for {symbol}: {instrument_key}")

        return instrument_key

    # ── Historical Candles ───────────────────────────────────────────────────

    def fetch_daily_candles(self, instrument_key: str, days: int = 365) -> list[dict]:
        """
        Fetch daily OHLCV candles for the given instrument.

        Args:
            instrument_key: Upstox instrument key (e.g. 'NSE_EQ|INE040A01034').
            days: Number of days of history (max 365 for v2 daily).

        Returns:
            List of dicts sorted chronologically (oldest first):
            [{'date': '2025-06-07', 'open': 1800.0, 'high': 1850.0,
              'low': 1790.0, 'close': 1840.0, 'volume': 1234567}, ...]
        """
        days = min(days, 365)  # v2 daily limit: 1 year
        return self._fetch_candles(instrument_key, "day", days)

    def fetch_weekly_candles(self, instrument_key: str, years: int = 2) -> list[dict]:
        """
        Fetch weekly OHLCV candles for the given instrument.

        Args:
            instrument_key: Upstox instrument key (e.g. 'NSE_EQ|INE040A01034').
            years: Number of years of history (max 10 for v2 weekly).

        Returns:
            List of dicts sorted chronologically (oldest first):
            [{'date': '2024-01-08', 'open': 1700.0, 'high': 1760.0,
              'low': 1680.0, 'close': 1750.0, 'volume': 5678900}, ...]
        """
        years = min(years, 10)  # v2 weekly limit: 10 years
        days = years * 365
        return self._fetch_candles(instrument_key, "week", days)

    def _fetch_candles(self, instrument_key: str, interval: str, days: int) -> list[dict]:
        """
        Internal method to fetch historical candles from Upstox.

        API response format: array of [timestamp, open, high, low, close, volume, OI]
        returned in reverse chronological order.
        """
        # URL-encode the pipe character in instrument key
        encoded_key = quote(instrument_key, safe="")
        to_date = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        symbol_display = instrument_key.split("|")[-1] if "|" in instrument_key else instrument_key
        print(f"[Upstox] Fetching {interval} candles for {symbol_display} ({from_date} to {to_date})...")

        url = f"{UPSTOX_BASE}/v2/historical-candle/{encoded_key}/{interval}/{to_date}/{from_date}"
        resp = self._request("GET", url)

        if resp.status_code != 200:
            print(f"[Upstox] [ERR] Candle fetch failed (HTTP {resp.status_code}): {resp.text}")
            return []

        data = resp.json()
        raw_candles = data.get("data", {}).get("candles", [])

        if not raw_candles:
            print(f"[Upstox] [WARN] No candle data returned for {instrument_key}")
            return []

        # Parse candles: [timestamp, open, high, low, close, volume, OI]
        candles = []
        for candle in raw_candles:
            if len(candle) < 6:
                continue
            candles.append({
                "date": candle[0][:10],   # Extract date from ISO timestamp
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": int(candle[5]),
            })

        # API returns reverse chronological — sort oldest first
        candles.sort(key=lambda c: c["date"])

        print(f"[Upstox] [OK] Fetched {len(candles)} {interval} candles")
        return candles

    # ── Live Quotes ──────────────────────────────────────────────────────────

    def fetch_ltp(self, instrument_key: str) -> float:
        """
        Fetch the last traded price for the given instrument.

        Args:
            instrument_key: Upstox instrument key (e.g. 'NSE_EQ|INE040A01034').

        Returns:
            The last traded price as a float.

        Raises:
            ValueError: If the LTP cannot be fetched.
        """
        encoded_key = quote(instrument_key, safe="")
        symbol_display = instrument_key.split("|")[-1] if "|" in instrument_key else instrument_key
        print(f"[Upstox] Fetching LTP for {symbol_display}...")

        url = f"{UPSTOX_BASE}/v2/market-quote/ltp"
        params = {"instrument_key": instrument_key}

        resp = self._request("GET", url, params=params)

        if resp.status_code != 200:
            raise ValueError(
                f"[Upstox] LTP fetch failed (HTTP {resp.status_code}): {resp.text}"
            )

        data = resp.json()
        quotes = data.get("data", {})

        # Response keyed by instrument_key (with pipe URL-encoded in some cases)
        # Try both the raw key and URL-encoded key
        quote_data = quotes.get(instrument_key) or quotes.get(encoded_key)

        if not quote_data:
            # Try the first available key in the response
            if quotes:
                first_key = next(iter(quotes))
                quote_data = quotes[first_key]
            else:
                raise ValueError(f"[Upstox] No quote data returned for {instrument_key}")

        ltp = quote_data.get("last_price", 0.0)
        print(f"[Upstox] [OK] LTP for {symbol_display}: ₹{ltp:.2f}")
        return float(ltp)

    def fetch_full_quote(self, instrument_key: str) -> dict:
        """
        Fetch a full market quote with OHLC, volume, and net change.

        Args:
            instrument_key: Upstox instrument key (e.g. 'NSE_EQ|INE040A01034').

        Returns:
            Dict with keys: last_price, ohlc, volume, net_change.
            Example:
            {
                'last_price': 1840.5,
                'ohlc': {'open': 1830.0, 'high': 1855.0, 'low': 1825.0, 'close': 1835.0},
                'volume': 2345678,
                'net_change': 5.5
            }

        Raises:
            ValueError: If the quote cannot be fetched.
        """
        encoded_key = quote(instrument_key, safe="")
        symbol_display = instrument_key.split("|")[-1] if "|" in instrument_key else instrument_key
        print(f"[Upstox] Fetching full quote for {symbol_display}...")

        url = f"{UPSTOX_BASE}/v2/market-quote/quotes"
        params = {"instrument_key": instrument_key}

        resp = self._request("GET", url, params=params)

        if resp.status_code != 200:
            raise ValueError(
                f"[Upstox] Quote fetch failed (HTTP {resp.status_code}): {resp.text}"
            )

        data = resp.json()
        quotes = data.get("data", {})

        # Find the quote data (key format may vary)
        quote_data = quotes.get(instrument_key) or quotes.get(encoded_key)

        if not quote_data:
            if quotes:
                first_key = next(iter(quotes))
                quote_data = quotes[first_key]
            else:
                raise ValueError(f"[Upstox] No quote data returned for {instrument_key}")

        ohlc_raw = quote_data.get("ohlc", {})
        result = {
            "last_price": float(quote_data.get("last_price", 0.0)),
            "ohlc": {
                "open": float(ohlc_raw.get("open", 0.0)),
                "high": float(ohlc_raw.get("high", 0.0)),
                "low": float(ohlc_raw.get("low", 0.0)),
                "close": float(ohlc_raw.get("close", 0.0)),
            },
            "volume": int(quote_data.get("volume", 0)),
            "net_change": float(quote_data.get("net_change", 0.0)),
        }

        print(f"[Upstox] [OK] {symbol_display}: ₹{result['last_price']:.2f} "
              f"(Chg: {result['net_change']:+.2f}, Vol: {result['volume']:,})")
        return result


# ── CLI Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys

    print("[Upstox] Starting client demo...")
    auth = UpstoxAuth()
    client = UpstoxClient(auth)

    symbol = _sys.argv[1] if len(_sys.argv) > 1 else "HDFCBANK"
    print(f"\n[Upstox] Demo: Resolving {symbol}...")

    try:
        key = client.resolve_instrument_key(symbol)
        print(f"[Upstox] Instrument Key: {key}")

        ltp = client.fetch_ltp(key)
        print(f"[Upstox] LTP: ₹{ltp:.2f}")

        full = client.fetch_full_quote(key)
        print(f"[Upstox] Full Quote: {json.dumps(full, indent=2)}")

        candles = client.fetch_daily_candles(key, days=30)
        print(f"[Upstox] Last 30 days: {len(candles)} candles")
        if candles:
            print(f"[Upstox] Latest: {candles[-1]}")

    except Exception as e:
        print(f"[Upstox] [ERR] {e}")
