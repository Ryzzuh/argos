"""Shared constants and paths.

The film id is the only thing that identifies the presentation: the slug in the
website URL is decorative. HO00000547 is the IMAX 70mm presentation;
HO00000546 is the 4K Laser one, and HO00000545 is an unrelated film entirely.
"""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
# DATA is scratch that can be thrown away between runs (token cache, seat
# layouts, the local history database). STATE is what has to survive a run to
# make the next diff meaningful; on an ephemeral cloud runner it is the only
# thing worth persisting, and it must never hold a credential.
DATA = Path(os.environ.get("IMAX_DATA_DIR", ROOT / "data"))
STATE = Path(os.environ.get("IMAX_STATE_DIR", DATA))
DOCS = ROOT / "docs"

DB_PATH = DATA / "argos.db"
TOKEN_CACHE = DATA / ".token.json"
LAYOUT_CACHE = DATA / "seat-layout-{layout_id}.json"
HEARTBEAT = STATE / "heartbeat.json"
ERROR_LOG = DATA / "errors.log"
ALERT_STATE = STATE / "alert-state.json"
CHROME_PROFILE = DATA / ".chrome-profile"
SEAT_STATE = STATE / "seats.json"

# Empty means "use Playwright's bundled Chromium" — the Linux CI runners
# have no Google Chrome channel to attach to.
BROWSER_CHANNEL = os.environ.get("IMAX_BROWSER_CHANNEL", "chrome")

# Every alert says where it came from. The laptop and a CI runner report into
# the same Telegram chat, and without this an alert from one is indis-
# tinguishable from the other - which turns a stray message into a wild goose
# chase through the wrong machine's logs.
def _source_label() -> str:
    if os.environ.get("IMAX_SOURCE"):
        return os.environ["IMAX_SOURCE"]
    if os.environ.get("GITHUB_ACTIONS"):
        repo = os.environ.get("GITHUB_REPOSITORY", "github")
        return f"github actions · {repo}"
    import socket
    return socket.gethostname().replace(".local", "")


SOURCE = _source_label()

REPORT_MD = ROOT / "REPORT.md"
# docs/index.html is the write-up and the GitHub Pages landing page;
# the live report is generated alongside it.
REPORT_HTML = DOCS / "report.html"

# Which films to watch lives in films.toml; see argos/films.py. This is the
# fallback for ad-hoc runs and the --film-id override.
FILM_ID = os.environ.get("IMAX_FILM_ID", "HO00000547")
SITE_ID = "IMAX"
FILMS_FILE = Path(os.environ.get("IMAX_FILMS_FILE", ROOT / "films.toml"))

WEB_BASE = "https://web.imaxmelbourne.com.au"
API_BASE = "https://digital-api.imaxmelbourne.com.au/ocapi/v1"

# The bearer token is anonymous and sits in __NEXT_DATA__ on *every* page, so
# minting does not need a film page - which is just as well, because the slug in
# a film URL is decorative and the id would otherwise have to be kept in step.
TOKEN_PAGE = WEB_BASE
TZ = ZoneInfo("Australia/Melbourne")

# Seat types that count as a real, bookable general-admission seat. Wheelchair
# and Companion spaces sit outside this set deliberately: they are almost always
# free, so alerting on them would fire constantly and mean nothing.
BOOKABLE_TYPES = frozenset({"Normal"})
ACCESSIBLE_TYPES = frozenset({"Wheelchair", "Companion"})
KNOWN_SEAT_TYPES = BOOKABLE_TYPES | ACCESSIBLE_TYPES
KNOWN_STATUSES = frozenset({"Available", "Sold", "House"})

# Baseline measured live on 2026-08-28. A departure from these is not
# necessarily a bug, but it is always worth being told about.
EXPECTED_LAYOUT_ID = "IMAX-1-26"
EXPECTED_SEAT_COUNT = 459

HEARTBEAT_STALE_MINUTES = 90

# How often each scheduled job runs. bin/install-agents.sh carries the same two
# numbers; a test keeps them in step. The watch interval is the budget for every
# run, full sweeps included, because the jobs share a lock: while any sweep is
# running the next scheduled one skips its turn, so a full sweep that outlasts the
# watch interval costs the sniping loop a tick.
WATCH_INTERVAL_SECONDS = 600
FULL_INTERVAL_SECONDS = 10800
# A run using more than this share of the budget leaves no headroom for a slow
# patch of rate limiting.
TIMING_WARN_FRACTION = 0.7


def booking_url(showtime_id: str) -> str:
    """Deep-link straight to the seat picker for a showtime."""
    return f"{WEB_BASE}/order/showtimes/{showtime_id}/seats"
