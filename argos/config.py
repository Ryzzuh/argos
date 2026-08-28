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

REPORT_MD = ROOT / "REPORT.md"
REPORT_HTML = DOCS / "index.html"

FILM_ID = os.environ.get("IMAX_FILM_ID", "HO00000547")
FILM_SLUG = "THE-ODYSSEY-IMAX-70MM-PRESENTATION"
SITE_ID = "IMAX"

WEB_BASE = "https://web.imaxmelbourne.com.au"
API_BASE = "https://digital-api.imaxmelbourne.com.au/ocapi/v1"

FILM_PAGE = f"{WEB_BASE}/films/{FILM_SLUG}/{FILM_ID}"
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


def booking_url(showtime_id: str) -> str:
    """Deep-link straight to the seat picker for a showtime."""
    return f"{WEB_BASE}/order/showtimes/{showtime_id}/seats"
