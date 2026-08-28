"""Failure reporting, heartbeats and the dead-man's switch.

The guiding rule: nothing fails silently. A scraper that quietly starts
returning "no seats" is indistinguishable from the truth here, because "no
seats" is also the correct answer right now. So schema drift is an error, not
an empty result, and a sweep that stops running has a separate process watching
for its absence.
"""
from __future__ import annotations

import fcntl
import json
import os
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import notify
from .config import (ALERT_STATE, DATA, ERROR_LOG, HEARTBEAT,
                     HEARTBEAT_STALE_MINUTES, TZ)

# While a failure persists, re-alert at most this often.
REALERT_AFTER = timedelta(hours=1)


class ArgosError(RuntimeError):
    """Base for failures worth telling a human about."""
    signature = "argos"


class TokenError(ArgosError):
    signature = "token"


class SchemaDrift(ArgosError):
    """The API no longer looks the way we expect. Never treat as 'no results'."""
    signature = "schema"


class SweepError(ArgosError):
    signature = "sweep"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local(ts: datetime) -> str:
    return ts.astimezone(TZ).strftime("%a %d %b %H:%M")


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _save(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    tmp.replace(path)


def log_error(text: str) -> None:
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ERROR_LOG.open("a") as fh:
        fh.write(f"{_now().isoformat()}  {text}\n")


# --------------------------------------------------------------------------
# failure de-duplication
# --------------------------------------------------------------------------

def report_failure(signature: str, message: str, *, detail: str = "") -> None:
    """Alert about a failure, at most hourly per signature."""
    state = _load(ALERT_STATE, {})
    failures = state.setdefault("failures", {})
    now = _now()
    entry = failures.get(signature)

    log_error(f"[{signature}] {message}" + (f" :: {detail}" if detail else ""))

    if entry:
        entry["count"] = entry.get("count", 1) + 1
        last = datetime.fromisoformat(entry["last_alert"])
        if now - last < REALERT_AFTER:
            _save(ALERT_STATE, state)      # count it, stay quiet
            return
        since = _local(datetime.fromisoformat(entry["first_seen"]))
        text = (f"⚠️ *Argos still failing* — `{signature}`\n\n{message}\n\n"
                f"Failing since {since} ({entry['count']} runs).")
    else:
        entry = {"first_seen": now.isoformat(), "count": 1}
        failures[signature] = entry
        text = f"⚠️ *Argos failed* — `{signature}`\n\n{message}"

    if detail:
        text += f"\n\n```\n{detail[:600]}\n```"

    entry["last_alert"] = now.isoformat()
    entry["message"] = message
    failures[signature] = entry

    if not notify.send(text):
        state["undelivered"] = state.get("undelivered", 0) + 1
    _save(ALERT_STATE, state)


def report_recovery(signature: str) -> None:
    """Announce that a previously failing thing works again."""
    state = _load(ALERT_STATE, {})
    failures = state.get("failures", {})
    entry = failures.pop(signature, None)
    if not entry:
        return

    since = _local(datetime.fromisoformat(entry["first_seen"]))
    text = (f"✅ *Argos recovered* — `{signature}`\n\n"
            f"Working again after {entry.get('count', 1)} failed runs since {since}.")

    undelivered = state.pop("undelivered", 0)
    if undelivered:
        text += f"\n\n({undelivered} alert(s) could not be delivered while Telegram was unreachable.)"

    notify.send(text)
    _save(ALERT_STATE, state)


# --------------------------------------------------------------------------
# heartbeat / dead-man's switch
# --------------------------------------------------------------------------

def write_heartbeat(outcome: str, **fields) -> None:
    _save(HEARTBEAT, {"at": _now().isoformat(), "outcome": outcome, **fields})


def check_heartbeat() -> int:
    """Watchdog entry point. Alerts if the sweep has stopped running."""
    beat = _load(HEARTBEAT, None)
    if beat is None:
        report_failure("watchdog", "No heartbeat file at all — has the sweep ever run?")
        return 1

    age = _now() - datetime.fromisoformat(beat["at"])
    if age > timedelta(minutes=HEARTBEAT_STALE_MINUTES):
        mins = int(age.total_seconds() // 60)
        report_failure(
            "watchdog",
            f"No sweep for {mins} min (last outcome: `{beat.get('outcome')}` "
            f"at {_local(datetime.fromisoformat(beat['at']))}).\n"
            "The monitor is not running — laptop asleep, or the launchd agent was unloaded.",
        )
        return 1

    report_recovery("watchdog")
    return 0


# --------------------------------------------------------------------------
# daily digest — so silence is never ambiguous
# --------------------------------------------------------------------------

def maybe_send_digest(summary: str) -> None:
    state = _load(ALERT_STATE, {})
    today = datetime.now(TZ).date().isoformat()
    if state.get("last_digest") == today or datetime.now(TZ).hour < 9:
        return
    if notify.send(f"📋 *Argos daily check-in*\n\n{summary}"):
        state["last_digest"] = today
        _save(ALERT_STATE, state)


# --------------------------------------------------------------------------
# top-level guard
# --------------------------------------------------------------------------

_LOCKS: list = []


def claim_single_instance(name: str = "sweep") -> None:
    """Ensure only one sweep runs at a time, or exit quietly.

    The watchlist and full-sweep schedules are harmonic - every third hour they
    fire together - and two concurrent sweeps simply exhaust the same
    41-request window and throttle each other into failure. The loser skips its
    turn rather than queueing, because by the next tick the data it wanted is
    already being fetched by the winner.
    """
    DATA.mkdir(parents=True, exist_ok=True)
    handle = (DATA / f".{name}.lock").open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        print("another sweep is already running; skipping this tick")
        raise SystemExit(0)
    # Held for the life of the process; the OS drops it on exit, including a
    # crash, so there is no stale lock to clean up.
    _LOCKS.append(handle)


@contextmanager
def guard(signature: str):
    """Wrap a run so any escaping exception is reported rather than swallowed."""
    try:
        yield
    except ArgosError as exc:
        report_failure(getattr(exc, "signature", signature), str(exc),
                       detail=traceback.format_exc())
        write_heartbeat("failed", error=str(exc))
        raise SystemExit(1)
    except Exception as exc:                       # noqa: BLE001 - deliberate catch-all
        report_failure(signature, f"Unexpected {type(exc).__name__}: {exc}",
                       detail=traceback.format_exc())
        write_heartbeat("failed", error=str(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(check_heartbeat())
