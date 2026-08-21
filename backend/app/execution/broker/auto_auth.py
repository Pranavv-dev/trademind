"""Unattended Zerodha Kite login via TOTP.

Kite Connect access tokens expire daily and Kite provides NO refresh token, so a
fresh interactive login is required every morning. This module automates that
login using stored credentials + a programmatically-generated TOTP code, so the
system can run 9:15-15:30 IST without anyone clicking the login URL.

Flow (mirrors what a human does in the browser):
  1. POST kite.zerodha.com/api/login    {user_id, password}        -> request_id
  2. POST kite.zerodha.com/api/twofa     {user_id, request_id, totp} -> session cookies
  3. GET  kite.zerodha.com/connect/login?api_key=...&v=3
         -> 302 chain ending in {redirect_url}?request_token=XXX
     (we walk the redirect chain and grab request_token from the Location header,
      so the redirect_url doesn't even need to be reachable)
  4. kite.generate_session(request_token, api_secret)              -> access_token

SECURITY: requires kite_password + kite_totp_secret in settings/.env. This is the
standard approach in the Indian retail-algo community for unattended trading, but
it does store sensitive credentials — keep .env gitignored and the host secured.
TOTP is generated with the stdlib (hmac/hashlib) — no external dependency.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import struct
import time
from functools import partial
from urllib.parse import parse_qs, urlparse

import httpx
import structlog

from app.config import settings

log = structlog.get_logger()

KITE_LOGIN_URL = "https://kite.zerodha.com/api/login"
KITE_TWOFA_URL = "https://kite.zerodha.com/api/twofa"


def _totp_now(secret: str, when: int | None = None) -> str:
    """Generate the current 6-digit TOTP from a base32 secret (RFC 6238, 30s step).

    Implemented with the standard library so we don't pull in pyotp.
    """
    # Normalize + pad base32 secret
    s = secret.strip().replace(" ", "").upper()
    s += "=" * ((8 - len(s) % 8) % 8)
    key = base64.b32decode(s)
    counter = int((when if when is not None else time.time()) // 30)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % 1_000_000).zfill(6)


async def _capture_request_token(client: httpx.AsyncClient, api_key: str) -> str | None:
    """Walk the connect/login redirect chain and pull request_token from a Location header."""
    url = f"https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"
    for _ in range(8):  # cap redirect hops
        resp = await client.get(url)
        # Already in this URL's query?
        qs = parse_qs(urlparse(str(resp.url)).query)
        if "request_token" in qs:
            return qs["request_token"][0]
        if resp.is_redirect:
            loc = resp.headers.get("location", "")
            if "request_token=" in loc:
                return parse_qs(urlparse(loc).query)["request_token"][0]
            if not loc:
                return None
            url = loc if loc.startswith("http") else f"https://kite.zerodha.com{loc}"
            continue
        # Non-redirect, no token — give up
        return None
    return None


async def auto_login() -> str | None:
    """Perform the full TOTP login and return a fresh access_token, or None on failure."""
    if not settings.kite_auto_auth_enabled:
        log.info("auto_login_disabled")
        return None

    user_id = settings.kite_user_id
    password = settings.kite_password
    totp_secret = settings.kite_totp_secret
    api_key = settings.kite_api_key
    api_secret = settings.kite_api_secret

    missing = [
        name
        for name, val in [
            ("kite_user_id", user_id),
            ("kite_password", password),
            ("kite_totp_secret", totp_secret),
            ("kite_api_key", api_key),
            ("kite_api_secret", api_secret),
        ]
        if not val
    ]
    if missing:
        log.warning("auto_login_missing_credentials", missing=missing)
        return None

    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(30.0, connect=15.0),
            headers={"User-Agent": "Mozilla/5.0 (compatible; TradeMind/1.0)"},
        ) as client:
            # 1. Password login
            r1 = await client.post(KITE_LOGIN_URL, data={"user_id": user_id, "password": password})
            if r1.status_code != 200:
                log.error("auto_login_step1_failed", status=r1.status_code, body=r1.text[:200])
                return None
            login_data = r1.json().get("data", {})
            request_id = login_data.get("request_id")
            if not request_id:
                log.error("auto_login_no_request_id", body=r1.text[:200])
                return None
            # Log the 2FA type the account actually uses (for diagnostics)
            log.info("auto_login_2fa_type", server_twofa_type=login_data.get("twofa_type"))

            # 2. TOTP two-factor. Do NOT force twofa_type — Zerodha rejects an
            # explicit type ("requested 2FA type is not available") and instead
            # uses whatever 2FA the account has configured. Send only the value.
            totp = _totp_now(totp_secret)
            r2 = await client.post(
                KITE_TWOFA_URL,
                data={
                    "user_id": user_id,
                    "request_id": request_id,
                    "twofa_value": totp,
                },
            )
            if r2.status_code != 200:
                log.error("auto_login_twofa_failed", status=r2.status_code, body=r2.text[:200])
                return None

            # 3. Grab request_token from the connect/login redirect chain
            request_token = await _capture_request_token(client, api_key)
            if not request_token:
                log.error("auto_login_no_request_token")
                return None

        # 4. Exchange request_token -> access_token (kiteconnect is sync)
        from kiteconnect import KiteConnect

        kite = KiteConnect(api_key=api_key)
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None, partial(kite.generate_session, request_token, api_secret=api_secret)
        )
        access_token = data.get("access_token")
        if not access_token:
            log.error("auto_login_no_access_token", data_keys=list(data.keys()))
            return None

        log.info("auto_login_success", user_id=data.get("user_id"))
        return access_token

    except Exception:
        log.exception("auto_login_error")
        return None


if __name__ == "__main__":
    # Manual test (bypasses the weekend/holiday guard in the Celery task):
    #   docker compose exec celery-worker python -m app.execution.broker.auto_auth
    import asyncio

    token = asyncio.run(auto_login())
    if token:
        print(f"AUTO_LOGIN_OK token_prefix={token[:8]}... (length {len(token)})")
    else:
        print("AUTO_LOGIN_FAILED — check creds in .env (see logs above for the failing step)")
