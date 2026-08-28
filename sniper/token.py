"""Mint and cache the OCAPI bearer token.

The website host sits behind Cloudflare and rejects every non-browser client
regardless of headers (it fingerprints the TLS handshake, so spoofing
User-Agent achieves nothing). The API host is not protected — it only wants the
token. So a real browser is needed exactly once per token lifetime (~12h), and
every request after that is plain HTTP.

The token is an anonymous client credential embedded in every page's
__NEXT_DATA__; it is not tied to a user account. It is cached with mode 600 and
never printed or logged.
"""
from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timezone

from .config import (BROWSER_CHANNEL, CHROME_PROFILE, FILM_PAGE,
                     TOKEN_CACHE, TZ)
from .health import TokenError

# Refresh this far ahead of expiry so a long sweep can't run off the end.
REFRESH_MARGIN_SECONDS = 15 * 60


def _decode_exp(token: str) -> int:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception as exc:
        raise TokenError(f"could not read exp claim from token: {exc}") from exc


def _cached() -> str | None:
    try:
        blob = json.loads(TOKEN_CACHE.read_text())
    except Exception:
        return None
    token, exp = blob.get("token"), blob.get("exp", 0)
    if not token or exp - time.time() < REFRESH_MARGIN_SECONDS:
        return None
    return token


def _store(token: str, exp: int) -> None:
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(json.dumps({"token": token, "exp": exp}))
    os.chmod(TOKEN_CACHE, 0o600)


_EXTRACT = """() => {
  const el = document.getElementById('__NEXT_DATA__');
  if (!el) return null;
  try {
    const d = JSON.parse(el.textContent);
    return (d.props && d.props.pageProps && d.props.pageProps.environment
            && d.props.pageProps.environment.gasToken) || null;
  } catch (e) { return null; }
}"""


def _mint_with(playwright, *, headless: bool):
    """Load the film page in Chrome and lift the token out of __NEXT_DATA__."""
    launch_kwargs = {"channel": BROWSER_CHANNEL} if BROWSER_CHANNEL else {}
    if headless:
        browser = playwright.chromium.launch(headless=True, **launch_kwargs)
        try:
            page = browser.new_page()
            page.goto(FILM_PAGE, wait_until="domcontentloaded", timeout=45_000)
            return page.evaluate(_EXTRACT)
        finally:
            browser.close()

    # Headful with a persistent profile: survives a Cloudflare interstitial,
    # because the clearance cookie is kept between runs.
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    ctx = playwright.chromium.launch_persistent_context(
        str(CHROME_PROFILE), headless=False, **launch_kwargs,
    )
    try:
        page = ctx.new_page()
        page.goto(FILM_PAGE, wait_until="domcontentloaded", timeout=60_000)
        token = page.evaluate(_EXTRACT)
        if token is None:                      # give a challenge time to clear
            page.wait_for_timeout(8_000)
            token = page.evaluate(_EXTRACT)
        return token
    finally:
        ctx.close()


def get_token(*, force: bool = False) -> str:
    """Return a valid bearer token, minting a new one only when needed."""
    if not force:
        cached = _cached()
        if cached:
            return cached

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise TokenError(
            "playwright is not installed — run `uv sync` in the project"
        ) from exc

    errors = []
    with sync_playwright() as pw:
        for headless in (True, False):
            try:
                token = _mint_with(pw, headless=headless)
            except Exception as exc:
                errors.append(f"{'headless' if headless else 'headful'}: "
                              f"{type(exc).__name__}: {exc}")
                continue
            if token:
                exp = _decode_exp(token)
                _store(token, exp)
                return token
            errors.append(
                f"{'headless' if headless else 'headful'}: page loaded but no "
                "gasToken in __NEXT_DATA__ (likely a Cloudflare challenge)"
            )

    raise TokenError(
        "could not mint an OCAPI token from " + FILM_PAGE + "\n" + "\n".join(errors)
    )


def token_expiry() -> datetime | None:
    try:
        exp = json.loads(TOKEN_CACHE.read_text())["exp"]
    except Exception:
        return None
    return datetime.fromtimestamp(exp, timezone.utc).astimezone(TZ)


if __name__ == "__main__":
    from .health import guard

    with guard("token"):
        get_token()
        expiry = token_expiry()
        # Deliberately prints the expiry only, never the token itself.
        print(f"token cached, valid until {expiry:%a %d %b %H:%M %Z}")
