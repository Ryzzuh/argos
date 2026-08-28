"""Render the latest sweep as REPORT.md and a self-contained status page."""
from __future__ import annotations

import json
from datetime import datetime

from . import store
from .config import DATA, REPORT_HTML, REPORT_MD, TZ, booking_url
from .token import token_expiry


def _load():
    snap = store.latest_full_snapshot()
    if snap is None:
        raise SystemExit("no snapshots yet — run `uv run python -m sniper.scrape` first")
    rows = store.snapshot_rows(snap["id"])

    seats: dict[str, list] = {}
    for s in store.free_seat_rows(snap["id"]):
        if s["seat_type"] == "Normal":
            seats.setdefault(s["showtime_id"], []).append(
                (s["row_label"], s["seat_label"], s["area"]))
    for v in seats.values():
        v.sort(key=lambda t: (t[0], int(t[1]) if str(t[1]).isdigit() else 0))

    try:
        summary = json.loads((DATA / "last-sweep.json").read_text())
    except Exception:
        summary = {}
    return snap, rows, seats, summary


def _fmt_seats(entries) -> str:
    return ", ".join(f"{row}{num}" + ("" if area == "Standard" else f" ({area})")
                     for row, num, area in entries)


def _when(iso: str) -> tuple[str, str]:
    dt = datetime.fromisoformat(iso).astimezone(TZ)
    return dt.strftime("%a %-d %b"), dt.strftime("%-I:%M%p").lower()


def _esc(t) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def render_markdown() -> str:
    snap, rows, seats, summary = _load()
    taken = datetime.fromisoformat(snap["taken_at"]).astimezone(TZ)
    open_rows = [r for r in rows if r["bookable"]]
    title = summary.get("title", "THE ODYSSEY - IMAX 70MM PRESENTATION")

    out: list[str] = []
    add = out.append

    add(f"# {title} — seat availability\n")
    add(f"*Swept {taken:%a %d %b %Y, %H:%M} (Melbourne). "
        f"Snapshot #{snap['id']}. {len(rows)} sessions checked.*\n")

    add("## The short version\n")
    if open_rows:
        add(f"**{summary.get('bookable_total', 0)} general-admission seat(s) free right "
            f"now, across {len(open_rows)} of {len(rows)} sessions.**\n")
        for r in open_rows:
            day, time_ = _when(r["starts_at"])
            add(f"- **{day} {time_}** — {r['bookable']} seat(s): "
                f"{_fmt_seats(seats.get(r['showtime_id'], []))} — "
                f"[book]({booking_url(r['showtime_id'])})")
        add("")
    else:
        add("**No general-admission seats are free in any session.**\n")

    add("Two things worth knowing about this listing:\n")
    add("1. **The film id in the website URL is what matters, not the slug.** "
        "`HO00000547` is the IMAX 70mm presentation. `HO00000546` is the 4K Laser "
        "version, and `HO00000545` is an unrelated film with no sessions at all — "
        "which is why that URL shows an empty date picker rather than an error.")
    add("2. **The site's own `isSoldOut` flag is not trustworthy.** "
        f"{sum(1 for r in rows if not r['sold_out_flag'])} of {len(rows)} sessions claim "
        "they are not sold out, but most of those have nothing free except the wheelchair "
        "and companion spaces in row M. Every number below is counted from the actual "
        "seat map instead.\n")

    add("## Every session\n")
    add("Accessible spaces are counted separately because they are almost always free and "
        "cannot be booked as ordinary seats. House seats are held back by the venue and "
        "are the most likely source of a late release.\n")
    add("| Session | Free to book | Accessible | House | Sold | |")
    add("|---|---:|---:|---:|---:|---|")
    for r in rows:
        day, time_ = _when(r["starts_at"])
        free = (f"**{r['bookable']}** ({_fmt_seats(seats.get(r['showtime_id'], []))})"
                if r["bookable"] else "—")
        add(f"| {day} {time_} | {free} | {r['accessible']} | {r['house']} | "
            f"{r['sold']} | [book]({booking_url(r['showtime_id'])}) |")
    add("")

    add("## The auditorium\n")
    add(f"Screen IMAX-1 seats {summary.get('seat_total', 459)}:\n")
    for key, count in (summary.get("breakdown") or {}).items():
        seat_type, _, area = key.partition("/")
        add(f"- {count} × {seat_type} ({area})")
    add("")

    add("## Where this data comes from\n")
    add("The site is a Next.js front end over Vista's Movie XChange OCAPI. All of it is "
        "read with plain authenticated GETs — no sign-in, no order, no seat holds:\n")
    add("| Endpoint | Purpose |")
    add("|---|---|")
    add("| `/films` | resolve title → film id |")
    add("| `/film-screening-dates` | which dates have sessions |")
    add("| `/showtimes/by-business-date/{date}` | sessions on a date |")
    add("| `/seat-layouts/{id}` | row/seat labels and seat types |")
    add("| `/showtimes/{id}/seat-availability` | per-seat Available / Sold / House |")
    add("")
    expiry = token_expiry()
    if expiry:
        add(f"Bearer token valid until {expiry:%a %d %b %H:%M}.")
    if summary.get("throttled_requests"):
        add(f"The API allows ~41 requests per rolling minute; this sweep absorbed "
            f"{summary['throttled_requests']} throttle response(s) and waited them out.")
    if summary.get("warnings"):
        add("\n**Warnings this sweep:**\n")
        for w in summary["warnings"]:
            add(f"- {w}")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

CSS = """
:root{
  --ground:#eceef1; --panel:#f7f8fa;
  --ink:#16191f; --ink-2:#4a5058; --ink-3:#787f89;
  --rule:#d3d7de; --amber:#a8641a;
  --good:#1f7a4d; --good-soft:#dcefe4;
  --shadow:0 1px 2px rgba(20,26,38,.07),0 8px 24px rgba(20,26,38,.05);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ground:#0e1116; --panel:#171b22;
    --ink:#e9ecf1; --ink-2:#a3abb8; --ink-3:#727b89;
    --rule:#2a313c; --amber:#e9a94f;
    --good:#5fce97; --good-soft:#14301f;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.3);
  }
}
:root[data-theme="dark"]{
  --ground:#0e1116; --panel:#171b22;
  --ink:#e9ecf1; --ink-2:#a3abb8; --ink-3:#727b89;
  --rule:#2a313c; --amber:#e9a94f;
  --good:#5fce97; --good-soft:#14301f;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.3);
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--ground); color:var(--ink);
  font-family:"IBM Plex Sans",ui-sans-serif,system-ui,-apple-system,sans-serif;
  font-size:16px; line-height:1.55; -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1000px; margin:0 auto; padding:32px 24px 72px;
      display:flex; flex-direction:column; gap:34px}
h1,h2{font-family:"Archivo",ui-sans-serif,system-ui,sans-serif; text-wrap:balance;
      margin:0; letter-spacing:-.015em}
h1{font-size:clamp(27px,4.2vw,41px); font-weight:700; line-height:1.08}
h2{font-size:19px; font-weight:600; letter-spacing:0}
p{margin:0}
a{color:var(--amber)}
a:focus-visible,.btn:focus-visible{outline:2px solid var(--amber); outline-offset:2px}
.mono{font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace}
.eyebrow{font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:11px;
  letter-spacing:.16em; text-transform:uppercase; color:var(--ink-3)}
/* 70mm sprocket rule - the one flourish, and it is the subject itself */
.sprocket{height:12px; border-radius:2px; opacity:.75; background:
  repeating-linear-gradient(90deg,var(--rule) 0 7px,transparent 7px 20px)}
header{display:flex; flex-direction:column; gap:13px}
.sub{color:var(--ink-2); font-size:14px}
.verdict{background:var(--panel); border:1px solid var(--rule); border-radius:10px;
  padding:22px 24px; box-shadow:var(--shadow);
  display:flex; flex-direction:column; gap:13px}
.verdict.hit{border-color:var(--good); background:var(--good-soft)}
.verdict h2{font-size:22px}
.hitlist{display:flex; flex-direction:column; gap:10px; margin:0; padding:0; list-style:none}
.hitlist li{display:flex; flex-wrap:wrap; align-items:center; gap:12px;
  padding:12px 14px; background:var(--panel); border:1px solid var(--rule);
  border-radius:8px}
.hitlist .when{font-weight:600}
.hitlist .seats{font-family:"IBM Plex Mono",monospace; font-size:13px; color:var(--ink-2)}
.btn{margin-left:auto; font-size:13px; font-weight:600; text-decoration:none;
  color:var(--panel); background:var(--amber); padding:6px 15px; border-radius:6px}
.btn:hover{filter:brightness(1.1)}
.tiles{display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(148px,1fr))}
.tile{background:var(--panel); border:1px solid var(--rule); border-radius:10px;
  padding:16px 18px; display:flex; flex-direction:column; gap:5px}
.tile .n{font-family:"Archivo",sans-serif; font-size:30px; font-weight:700;
  font-variant-numeric:tabular-nums; line-height:1}
.tile .n.good{color:var(--good)}
.tile .l{font-size:12px; color:var(--ink-3)}
section{display:flex; flex-direction:column; gap:14px}
.note{color:var(--ink-2); font-size:14px; max-width:68ch}
.scroll{overflow-x:auto; border:1px solid var(--rule); border-radius:10px;
  background:var(--panel)}
table{border-collapse:collapse; width:100%; font-size:14px}
th,td{padding:9px 14px; text-align:left; border-bottom:1px solid var(--rule);
  white-space:nowrap}
th{font-family:"IBM Plex Mono",monospace; font-size:10.5px; letter-spacing:.12em;
  text-transform:uppercase; color:var(--ink-3); font-weight:500;
  position:sticky; top:0; background:var(--panel)}
th.num,td.num{text-align:right}
td.num{font-variant-numeric:tabular-nums; font-family:"IBM Plex Mono",monospace;
  font-size:13px; color:var(--ink-2)}
tbody tr:last-child td{border-bottom:none}
tr.open td{background:var(--good-soft)}
tr.past td{opacity:.45}
.chip{display:inline-block; font-family:"IBM Plex Mono",monospace; font-size:11px;
  padding:2px 9px; border-radius:999px; border:1px solid var(--rule); color:var(--ink-3)}
.chip.free{background:var(--good-soft); border-color:var(--good); color:var(--good);
  font-weight:600}
td a{font-size:13px}
footer{color:var(--ink-3); font-size:13px; display:flex; flex-direction:column; gap:10px}
footer code{font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-2)}
@media (max-width:560px){.wrap{padding:22px 14px 56px} .btn{margin-left:0}}
"""


def render_html() -> str:
    snap, rows, seats, summary = _load()
    taken = datetime.fromisoformat(snap["taken_at"]).astimezone(TZ)
    now = datetime.now(TZ)
    open_rows = [r for r in rows if r["bookable"]]
    title = summary.get("title", "THE ODYSSEY - IMAX 70MM PRESENTATION")
    bookable = summary.get("bookable_total", sum(r["bookable"] for r in rows))

    if open_rows:
        items = "".join(
            '<li><span class="when">{} · {}</span>'
            '<span class="seats">{}</span>'
            '<a class="btn" href="{}">Book</a></li>'.format(
                *_when(r["starts_at"]),
                _esc(_fmt_seats(seats.get(r["showtime_id"], []))),
                booking_url(r["showtime_id"]))
            for r in open_rows)
        verdict = (
            '<div class="verdict hit"><h2>{} seat{} free right now</h2>'
            '<p class="note">Across {} of {} sessions. These are ordinary seats — '
            'wheelchair and companion spaces are counted separately below.</p>'
            '<ul class="hitlist">{}</ul></div>'
        ).format(bookable, "" if bookable == 1 else "s", len(open_rows), len(rows), items)
    else:
        verdict = (
            '<div class="verdict"><h2>No general-admission seats free</h2>'
            '<p class="note">All {} sessions are sold out. The only seats the booking '
            'site shows as available are the wheelchair and companion spaces in row M, '
            'which are not ordinary tickets — which is why the seat map looks empty even '
            'on sessions the site says are not sold out.</p></div>').format(len(rows))

    tiles = [
        (bookable, "seats free to book", "good" if bookable else ""),
        (len(rows), "sessions tracked", ""),
        (summary.get("accessible_total", 0), "accessible spaces", ""),
        (summary.get("house_total", 0), "held by the venue", ""),
    ]
    tile_html = "".join(
        f'<div class="tile"><span class="n {cls}">{n}</span>'
        f'<span class="l">{label}</span></div>' for n, label, cls in tiles)

    body_rows = []
    for r in rows:
        day, time_ = _when(r["starts_at"])
        past = datetime.fromisoformat(r["starts_at"]).astimezone(TZ) < now
        cls = " ".join(c for c in ("open" if r["bookable"] else "",
                                   "past" if past else "") if c)
        chip = (f'<span class="chip free">{_esc(_fmt_seats(seats.get(r["showtime_id"], [])))}</span>'
                if r["bookable"] else '<span class="chip">sold out</span>')
        body_rows.append(
            f'<tr class="{cls}"><td>{day}</td><td class="mono">{time_}</td>'
            f'<td>{chip}</td><td class="num">{r["accessible"]}</td>'
            f'<td class="num">{r["house"]}</td><td class="num">{r["sold"]}</td>'
            f'<td><a href="{booking_url(r["showtime_id"])}">book</a></td></tr>')

    warn = ('<p class="note">⚠ ' + "; ".join(_esc(w) for w in summary["warnings"]) + "</p>"
            if summary.get("warnings") else "")

    heading = _esc(title.title().replace("Imax", "IMAX").replace("70Mm", "70mm"))

    return f"""<meta charset="utf-8">
<title>Odyssey 70mm Seat Watch</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;600&display=swap">
<style>{CSS}</style>
<div class="wrap">
  <header>
    <span class="eyebrow">IMAX Melbourne · Screen 1 · 70mm film</span>
    <h1>{heading}</h1>
    <div class="sprocket"></div>
    <p class="sub">Last checked {taken:%a %-d %b, %-I:%M%p} · snapshot #{snap['id']} ·
      {len(rows)} sessions counted from the live seat map, not the site's sold-out flag.</p>
  </header>

  {verdict}

  <div class="tiles">{tile_html}</div>

  <section>
    <h2>Every session</h2>
    <p class="note">Accessible spaces are listed apart because they are nearly always free
      and are not ordinary tickets. House seats are held back by the venue and are the
      most likely source of a late release.</p>
    <div class="scroll"><table>
      <thead><tr><th>Date</th><th>Time</th><th>Free to book</th>
        <th class="num">Access.</th><th class="num">House</th><th class="num">Sold</th>
        <th></th></tr></thead>
      <tbody>{"".join(body_rows)}</tbody>
    </table></div>
  </section>

  <footer>
    <div class="sprocket"></div>
    <p>Read from Vista Movie XChange OCAPI with plain authenticated GETs — no sign-in, no
      order, no seat holds. The film is <code>HO00000547</code>; the slug in the website
      URL is decorative, which is how <code>HO00000545</code> (a different film entirely)
      ends up showing an empty date picker.</p>
    {warn}
  </footer>
</div>
"""


def main() -> None:
    REPORT_MD.write_text(render_markdown())
    REPORT_HTML.parent.mkdir(parents=True, exist_ok=True)
    REPORT_HTML.write_text(render_html())
    print(f"wrote {REPORT_MD}")
    print(f"wrote {REPORT_HTML}")


if __name__ == "__main__":
    main()
