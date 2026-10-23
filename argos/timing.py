"""Whether sweeps still fit inside their schedule, measured rather than predicted.

The failure this guards against is quiet. The scheduled jobs share a lock, so
while one sweep is running the next scheduled one prints "another sweep is
already running" to a log file and skips its turn. Nothing else notices: the
heartbeat stays fresh and the watchdog only fires after ninety minutes of
silence. A monitor whose sweeps have grown past its schedule just gets slower,
and alerts arrive late.

So each run records how long it took, and alerts when the margin has gone.

The budget is the watch interval for every kind of run, full sweeps included.
That is deliberate: a full sweep that takes longer than the watch interval
blocks the sniping loop for as long as it runs, even though a full sweep only
starts every three hours.

    overran   the latest run took longer than the budget. A tick was skipped.
    tight     the last three watch runs each used more than 70% of the budget.
              One slow patch of rate limiting away from overrunning.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from . import health
from .config import STATE, TIMING_WARN_FRACTION, TZ, WATCH_INTERVAL_SECONDS

TIMING_FILE = STATE / "timing.json"
BUDGET = WATCH_INTERVAL_SECONDS
KEEP = 10          # runs remembered per kind
ROUTINE = 3        # consecutive tight runs that make it a pattern, not a blip
KINDS = ("watch", "full")


def fmt(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


# --------------------------------------------------------------------------
# the rule
# --------------------------------------------------------------------------

def assess(kind: str, durations: list[float], budget: float = BUDGET,
           warn_fraction: float = TIMING_WARN_FRACTION) -> tuple[str | None, str]:
    """('alert', reason), ('recover', '') or (None, '') for this run history.

    `durations` is oldest first. Recovery needs only the latest run to be back
    under the line, so a fixed problem is announced promptly rather than after
    several more runs.
    """
    if not durations:
        return None, ""
    latest = durations[-1]
    if latest > budget:
        return "alert", "overran"
    recent = durations[-ROUTINE:]
    if (kind == "watch" and len(recent) == ROUTINE
            and all(d > warn_fraction * budget for d in recent)):
        return "alert", "tight"
    if latest <= warn_fraction * budget:
        return "recover", ""
    return None, ""


# --------------------------------------------------------------------------
# history
# --------------------------------------------------------------------------

def load() -> dict[str, list[dict]]:
    try:
        raw = json.loads(TIMING_FILE.read_text())
    except Exception:
        return {k: [] for k in KINDS}
    return {k: [e for e in raw.get(k, []) if isinstance(e, dict) and "seconds" in e]
            for k in KINDS}


def record(kind: str, seconds: float, films: list[dict]) -> list[dict]:
    """Append this run and return the kind's history, oldest first."""
    history = load()
    history[kind].append({
        "at": datetime.now(timezone.utc).isoformat(),
        "seconds": round(seconds, 1),
        "requests": sum(f.get("requests") or 0 for f in films),
        "throttled": sum(f.get("throttled") or 0 for f in films),
        "films": films,
    })
    history[kind] = history[kind][-KEEP:]
    TIMING_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TIMING_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(history, indent=1))
    tmp.replace(TIMING_FILE)
    return history[kind]


# --------------------------------------------------------------------------
# telling someone
# --------------------------------------------------------------------------

def message(kind: str, reason: str, entries: list[dict]) -> str:
    latest = entries[-1]
    if reason == "overran" and kind == "full":
        head = (f"The last full sweep took {fmt(latest['seconds'])}, longer than the "
                f"{fmt(BUDGET)} between watch sweeps. They share a lock, so the "
                "watch sweep skipped its turn for as long as it ran.")
    elif reason == "overran":
        head = (f"The last watch sweep took {fmt(latest['seconds'])}, longer than the "
                f"{fmt(BUDGET)} between sweeps. While one runs the next is skipped, "
                "so alerts are arriving later than they should.")
    else:
        recent = entries[-ROUTINE:]
        runs = ", ".join(fmt(e["seconds"]) for e in recent)
        head = (f"The last {len(recent)} watch sweeps took {runs} against a "
                f"{fmt(BUDGET)} schedule. That leaves no headroom: one slow patch of "
                "rate limiting and the next sweep is skipped.")

    lines = [head, ""]
    for f in sorted(latest.get("films", []), key=lambda f: -(f.get("seconds") or 0)):
        lines.append(f"• {f.get('name', f.get('id'))}: {fmt(f.get('seconds') or 0)}, "
                     f"{f.get('requests') or 0} requests")
    lines.append(f"\n{latest['requests']} requests in all, {latest['throttled']} of them "
                 "throttled by the API.")
    lines.append("\nTo bring it down, make each sweep cover less: fewer films, a shorter "
                 "`within_days`, or `lead_days` for films that have not started. "
                 "`uv run python -m argos.films` shows the current settings.")
    return "\n".join(lines)


def record_and_check(kind: str, seconds: float, films: list[dict]) -> None:
    """Record this run, then alert or announce recovery as the history warrants."""
    entries = record(kind, seconds, films)
    verdict, reason = assess(kind, [e["seconds"] for e in entries])
    signature = f"timing:{kind}"
    if verdict == "alert":
        health.report_failure(signature, message(kind, reason, entries))
    elif verdict == "recover":
        health.report_recovery(signature)


def summary_line() -> str:
    """For the daily check-in, so a healthy monitor also says how much slack it has."""
    history = load()
    parts = []
    for kind in KINDS:
        if history[kind]:
            last = history[kind][-1]["seconds"]
            parts.append(f"{kind} {fmt(last)} ({last / BUDGET:.0%} of budget)")
    return "Sweep time: " + ", ".join(parts) + "." if parts else ""


# --------------------------------------------------------------------------
# looking at it
# --------------------------------------------------------------------------

def main() -> None:
    argparse.ArgumentParser(description="Recent sweep durations against the budget").parse_args()
    history = load()
    print(f"budget: {fmt(BUDGET)} (the watch interval; every kind of run shares it)\n")
    if not any(history.values()):
        print("no runs recorded yet — the scheduled agents record one each time they run")
        return
    for kind in KINDS:
        entries = history[kind]
        print(f"{kind}: {len(entries)} run(s) recorded")
        for e in entries[-5:]:
            at = datetime.fromisoformat(e["at"]).astimezone(TZ)
            share = e["seconds"] / BUDGET
            print(f"  {at:%a %-d %b %H:%M}  {fmt(e['seconds']):>7}  {share:>5.0%} of budget  "
                  f"{e['requests']:>4} requests  {e['throttled']:>3} throttled")
        verdict, reason = assess(kind, [e["seconds"] for e in entries])
        state = {"alert": f"ALERTING ({reason})", "recover": "healthy"}.get(verdict, "within budget")
        print(f"  -> {state}\n")


if __name__ == "__main__":
    main()
