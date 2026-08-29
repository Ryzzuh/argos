"""Telegram delivery.

Credentials live outside the repo, in the Claude Code Telegram channel config
(~/.claude/channels/telegram). They are read at call time and never logged, so
nothing secret lands in this project or in version control.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import httpx

from .config import SOURCE

CHANNEL_DIR = Path.home() / ".claude" / "channels" / "telegram"
ENV_FILE = CHANNEL_DIR / ".env"
ACCESS_FILE = CHANNEL_DIR / "access.json"

API = "https://api.telegram.org"


class NotifyError(RuntimeError):
    pass


def _bot_token() -> str:
    # CI has no Claude Code config; the workflow supplies these as secrets.
    if env := os.environ.get("TELEGRAM_BOT_TOKEN"):
        return env
    if not ENV_FILE.exists():
        raise NotifyError(f"no Telegram bot token at {ENV_FILE}")
    for line in ENV_FILE.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "TELEGRAM_BOT_TOKEN" and value.strip():
            return value.strip().strip("'\"")
    raise NotifyError(f"TELEGRAM_BOT_TOKEN not set in {ENV_FILE}")


def _chat_id() -> str:
    if env := os.environ.get("TELEGRAM_CHAT_ID"):
        return env
    if not ACCESS_FILE.exists():
        raise NotifyError(f"no Telegram access config at {ACCESS_FILE}")
    allow = json.loads(ACCESS_FILE.read_text()).get("allowFrom") or []
    if not allow:
        raise NotifyError("no approved Telegram chat in allowFrom")
    return str(allow[0])


def desktop_fallback(text: str) -> None:
    """Last resort when Telegram itself is unreachable."""
    body = text.replace('"', "'").replace("\n", " ")[:400]
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{body}" with title "Argos" sound name "Sosumi"'],
            check=False, capture_output=True, timeout=10,
        )
    except Exception:
        pass


def send(text: str, *, disable_preview: bool = True) -> bool:
    """Send a Markdown message, tagged with the machine that sent it.

    Returns True on delivery.

    Never raises: a failure here must not take down the sweep that was trying to
    report something. Falls back to a desktop notification and lets the caller
    record the gap.
    """
    text = f"{text}\n\n`— {SOURCE}`"
    try:
        token, chat = _bot_token(), _chat_id()
    except NotifyError as exc:
        desktop_fallback(f"Telegram misconfigured: {exc}")
        return False

    try:
        resp = httpx.post(
            f"{API}/bot{token}/sendMessage",
            json={
                "chat_id": chat,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": disable_preview,
            },
            timeout=20,
        )
    except Exception as exc:
        desktop_fallback(f"Telegram send failed: {type(exc).__name__}")
        return False

    if resp.status_code != 200:
        # Markdown parse errors are the common cause; retry as plain text so the
        # content still gets through.
        try:
            retry = httpx.post(
                f"{API}/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text,
                      "disable_web_page_preview": disable_preview},
                timeout=20,
            )
            if retry.status_code == 200:
                return True
        except Exception:
            pass
        desktop_fallback(f"Telegram rejected message: HTTP {resp.status_code}")
        return False

    return True


if __name__ == "__main__":
    ok = send("*Argos* — notification test. If you can read this, alerts work.")
    print("delivered" if ok else "FAILED (check desktop notification)")
