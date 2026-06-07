"""
upstox_auth.py - Upstox API v2 Authentication Helper
=====================================================
PURPOSE:
  Handles the full OAuth2 flow for the Upstox API:
  1. Opens the authorization URL in the user's default browser
  2. Spins up a tiny local HTTP server to catch the callback
  3. Exchanges the auth code for an access token
  4. Caches the token in sessions/upstox_token.json

  Tokens expire daily at 3:30 AM IST. The module checks this
  automatically and re-authenticates when needed.

SETUP:
  1. Go to https://account.upstox.com/developer/apps
  2. Create a new app with redirect URI: http://127.0.0.1:5000/callback
  3. Copy API key and secret into config/upstox_config.json

USAGE:
  from src.upstox_auth import UpstoxAuth
  auth = UpstoxAuth()
  token = auth.get_access_token()        # auto-login if needed
  headers = auth.get_headers()           # {'Authorization': 'Bearer ...', ...}
"""

import json
import sys
import webbrowser
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests

# ── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent
CONFIG_FILE = BASE_DIR / "config" / "upstox_config.json"
TOKEN_FILE = BASE_DIR / "sessions" / "upstox_token.json"

# ── Constants ────────────────────────────────────────────────────────────────

UPSTOX_BASE = "https://api.upstox.com"
AUTH_DIALOG_URL = f"{UPSTOX_BASE}/v2/login/authorization/dialog"
TOKEN_URL = f"{UPSTOX_BASE}/v2/login/authorization/token"

# IST = UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))


# ── Callback Handler ─────────────────────────────────────────────────────────

class _CallbackHandler(BaseHTTPRequestHandler):
    """Tiny HTTP handler that captures the OAuth callback code."""

    auth_code: str | None = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "code" in params:
            _CallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Login successful!</h2>"
                b"<p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )
            print("[UpstoxAuth] [OK] Authorization code received.")
        else:
            error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<html><body><h2>Error: {error}</h2></body></html>".encode())
            print(f"[UpstoxAuth] [ERR] Callback error: {error}")

    def log_message(self, format, *args):
        """Suppress default HTTP server logging."""
        pass


# ── Auth Class ───────────────────────────────────────────────────────────────

class UpstoxAuth:
    def __init__(self):
        self.config = self._load_config()
        self._token_data: dict | None = None
        self._load_cached_token()

    # ── Config Loading ───────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        """Load Upstox config from config/upstox_config.json."""
        if not CONFIG_FILE.exists():
            print(f"[UpstoxAuth] [ERR] Config not found: {CONFIG_FILE}")
            print("[UpstoxAuth] Create config/upstox_config.json with api_key, api_secret, redirect_uri")
            sys.exit(1)

        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)

        # Bypass validation if direct access token is present
        direct_token = config.get("access_token")
        if direct_token and not direct_token.startswith("YOUR_"):
            return config

        # Validate required fields
        for key in ("api_key", "api_secret", "redirect_uri"):
            if not config.get(key) or config[key].startswith("YOUR_"):
                print(f"[UpstoxAuth] [ERR] '{key}' not configured in {CONFIG_FILE}")
                print(f"[UpstoxAuth] Get credentials from https://account.upstox.com/developer/apps or configure direct 'access_token'")
                sys.exit(1)

        return config

    # ── Token Caching ────────────────────────────────────────────────────────

    def _load_cached_token(self):
        """Load cached token from sessions/upstox_token.json if it exists."""
        if TOKEN_FILE.exists():
            try:
                with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                    self._token_data = json.load(f)
                print("[UpstoxAuth] Loaded cached token.")
            except (json.JSONDecodeError, IOError):
                self._token_data = None

    def _save_token(self, token_data: dict):
        """Save token to sessions/upstox_token.json with timestamp."""
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)

        token_data["obtained_at"] = datetime.now(IST).isoformat()
        self._token_data = token_data

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(token_data, f, indent=2)

        print(f"[UpstoxAuth] Token cached at {TOKEN_FILE}")

    # ── Token Validity ───────────────────────────────────────────────────────

    def is_token_valid(self) -> bool:
        """
        Check if the cached token is still valid.

        Upstox tokens expire at 3:30 AM IST daily. A token is valid if:
        - It exists in the cache
        - It was obtained after the most recent 3:30 AM IST cutoff
        """
        if not self._token_data or "access_token" not in self._token_data:
            return False

        obtained_str = self._token_data.get("obtained_at")
        if not obtained_str:
            return False

        try:
            obtained = datetime.fromisoformat(obtained_str)
            # Ensure timezone-aware
            if obtained.tzinfo is None:
                obtained = obtained.replace(tzinfo=IST)
        except ValueError:
            return False

        now_ist = datetime.now(IST)

        # Calculate the most recent 3:30 AM IST cutoff
        cutoff_today = now_ist.replace(hour=3, minute=30, second=0, microsecond=0)
        if now_ist >= cutoff_today:
            # We're past today's 3:30 AM, so the cutoff is today's 3:30 AM
            cutoff = cutoff_today
        else:
            # We're before today's 3:30 AM, so the cutoff is yesterday's 3:30 AM
            cutoff = cutoff_today - timedelta(days=1)

        return obtained > cutoff

    # ── Public API ───────────────────────────────────────────────────────────

    def get_access_token(self) -> str:
        """
        Get a valid access token. Uses direct config token if provided,
        otherwise uses cached token if valid, otherwise triggers login flow.

        Returns:
            The access token string.
        """
        # 1. Use direct access token if configured in upstox_config.json
        direct_token = self.config.get("access_token")
        if direct_token and not direct_token.startswith("YOUR_"):
            print("[UpstoxAuth] [OK] Using direct access_token from config.")
            return direct_token

        # 2. Use cached token if available and still valid
        if self.is_token_valid():
            print("[UpstoxAuth] [OK] Using cached token (still valid).")
            return self._token_data["access_token"]

        print("[UpstoxAuth] Token expired or missing. Starting login flow...")
        return self.login()

    def login(self) -> str:
        """
        Full OAuth2 login flow:
        1. Opens the Upstox auth URL in the default browser
        2. Starts a local HTTP server on port 5000 to catch the callback
        3. Extracts the authorization code from the callback
        4. Exchanges the code for an access token
        5. Caches the token for future use

        Returns:
            The access token string.
        """
        api_key = self.config["api_key"]
        redirect_uri = self.config["redirect_uri"]

        # Build authorization URL
        auth_url = (
            f"{AUTH_DIALOG_URL}"
            f"?response_type=code"
            f"&client_id={api_key}"
            f"&redirect_uri={redirect_uri}"
        )

        # Reset the auth code
        _CallbackHandler.auth_code = None

        # Start local HTTP server to catch callback
        server = HTTPServer(("127.0.0.1", 5000), _CallbackHandler)
        server_thread = threading.Thread(target=server.handle_request, daemon=True)
        server_thread.start()

        # Open browser for user login
        print("[UpstoxAuth] Opening browser for Upstox login...")
        print(f"[UpstoxAuth] Auth URL: {auth_url}")
        webbrowser.open(auth_url)

        print("[UpstoxAuth] Waiting for callback on http://127.0.0.1:5000/callback ...")
        server_thread.join(timeout=300)  # 5 minute timeout
        server.server_close()

        if not _CallbackHandler.auth_code:
            print("[UpstoxAuth] [ERR] No authorization code received. Login timed out or was cancelled.")
            sys.exit(1)

        # Exchange code for token
        return self._exchange_code(_CallbackHandler.auth_code)

    def _exchange_code(self, auth_code: str) -> str:
        """Exchange the authorization code for an access token."""
        print("[UpstoxAuth] Exchanging code for access token...")

        payload = {
            "code": auth_code,
            "client_id": self.config["api_key"],
            "client_secret": self.config["api_secret"],
            "redirect_uri": self.config["redirect_uri"],
            "grant_type": "authorization_code",
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        try:
            resp = requests.post(TOKEN_URL, data=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            access_token = data.get("access_token")
            if not access_token:
                print(f"[UpstoxAuth] [ERR] No access_token in response: {data}")
                sys.exit(1)

            self._save_token(data)
            print("[UpstoxAuth] [OK] Login successful! Token cached.")
            return access_token

        except requests.exceptions.HTTPError as e:
            print(f"[UpstoxAuth] [ERR] Token exchange failed (HTTP {resp.status_code}): {resp.text}")
            raise
        except requests.exceptions.RequestException as e:
            print(f"[UpstoxAuth] [ERR] Token exchange request failed: {e}")
            raise

    def get_headers(self) -> dict:
        """
        Return HTTP headers for authenticated Upstox API calls.

        Returns:
            Dict with Authorization bearer token and Accept header.
        """
        token = self.get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }


# ── CLI Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("[UpstoxAuth] Starting authentication flow...")
    auth = UpstoxAuth()
    token = auth.get_access_token()
    print(f"\n[UpstoxAuth] Access Token: {token[:20]}...{token[-10:]}")
    print("[UpstoxAuth] Token is valid and cached.")
