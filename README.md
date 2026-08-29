# Argos

> Odysseus's dog, who waited twenty years on a dung heap and was the only one to
> know his master the instant he walked through the gate. Also Argus Panoptes,
> the hundred-eyed watchman who never closed every eye at once — though Hermes
> did eventually kill him by lulling him to sleep, which is precisely this
> tool's failure mode when the laptop shuts.

Watches seat availability for **THE ODYSSEY — IMAX 70MM PRESENTATION** at IMAX
Melbourne and pushes a Telegram alert the moment a genuinely bookable seat is
released.

It alerts only. It never selects seats, creates an order, or buys anything —
you complete checkout yourself from the link in the alert.

## Two things that make this film hard to book

**The film id in the URL is what matters, the slug is decorative.**
`HO00000547` is the IMAX 70mm presentation. `HO00000546` is the 4K Laser
version, and `HO00000545` is an unrelated film with no sessions at all — point a
URL at that id and the site renders an empty date picker rather than an error,
which reads as "no tickets" when it actually means "wrong film".

**The site's `isSoldOut` flag is wrong.** Around 34 of 77 sessions report
`isSoldOut: false` while having nothing free but the four wheelchair spaces and
two companion seats in row M. Those are not ordinary tickets, which is why the
seat map looks empty on a session the site insists is available. Every number
this tool reports is counted from the actual seat map and split by seat type.

## Usage

```bash
uv sync                                    # once
uv run python -m argos.token              # mint a token (prints expiry only)
uv run python -m argos.scrape             # one full sweep -> SQLite
uv run python -m argos.report             # REPORT.md + docs/index.html
uv run python -m argos.watch              # sweep, diff, alert, re-render
uv run python -m argos.health             # watchdog: is the monitor alive?
```

Useful flags:

| Flag | Effect |
|---|---|
| `--within-days N` | only check sessions N days out (a watchlist run) |
| `--dry-run` | sweep and diff, send nothing |
| `--replay N` | re-send snapshot N's alert, marked as a test |
| `--no-report` | skip re-rendering the page |
| `--film-id ID` | point at a different film |

## What triggers an alert

1. **A seat is released** — a `Normal`-type seat that was not free on the
   previous sweep. Wheelchair and companion spaces are excluded; they are free
   almost permanently and would fire every run.
2. **A new screening is added** — a session that has just appeared in the
   schedule with seats free. This needs its own detector: a session that did not
   exist before has no previous state to differ from, so from the second sweep
   onward the seat diff considers all of its seats "unchanged" and says nothing.
   For a sold-out run, an added 70mm screening is the likeliest way a ticket
   becomes available at all.

A new screening *date* is caught on the next sweep, because the date list is the
showtime cache's key. A new screening on a date that already has sessions is
caught by the next full sweep, which re-reads the schedule outright.

## Scheduling

```bash
cp launchd/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.rhys.argos.{watch,full,watchdog}.plist
```

- **watch** — next 14 days, every 10 min. This is the sniping loop.
- **full** — all sessions, every 3 h. Feeds the report page.
- **watchdog** — every 2 h, alerts if no sweep has run in 90 min. Deliberately a
  separate process, because the failure it catches is the other two not running.

To unload: `launchctl unload ~/Library/LaunchAgents/com.rhys.argos.*.plist`

## Why this does not run in the cloud

It was tried and it does not work. `.github/workflows/watch.yml` is kept, with
its schedule disabled, so the finding stays reproducible via **Run workflow**.

A GitHub runner is served a Cloudflare challenge instead of the page, so there
is no token to extract:

```
headless: page loaded but no gasToken in __NEXT_DATA__ (likely a Cloudflare challenge)
```

The runner used real headless Chrome with a genuine browser TLS fingerprint, so
this is **IP reputation, not client detection** — which means no VPS will fix
it either. Only a residential connection gets through. `sniper/cloud.py` and the
`state/seats.json` diff baseline still work and are exercised locally; they
would come alive on any always-on machine at home.

The practical consequence: **while this Mac is asleep or off, nothing is
watched.** launchd agents only run when the machine is awake and logged in, and
on wake launchd fires a single catch-up run rather than one per missed
interval. That gap is a deliberate, accepted trade-off, not an oversight. The
watchdog reports it after the fact, so expect a "no sweep for N minutes"
message most mornings — it is telling you exactly which window went unwatched.

The published report page is likewise only as fresh as the last time it was
deployed; `docs/index.html` on disk is regenerated every full sweep and can be
opened directly.

## How it gets the data## How it gets the data

A Next.js front end ("Lumos") over Vista's Movie XChange OCAPI at
`digital-api.imaxmelbourne.com.au/ocapi/v1`. Seat availability is a plain
authenticated GET — no sign-in, no order, no guest checkout.

| Endpoint | Purpose |
|---|---|
| `/films` | resolve title → film id |
| `/film-screening-dates` | which dates have sessions |
| `/showtimes/by-business-date/{date}` | sessions on a date (cached 6 h) |
| `/seat-layouts/{id}` | row/seat labels and seat types (cached) |
| `/showtimes/{id}/seat-availability` | per-seat `Available` / `Sold` / `House` |

Auth is a ~12 h anonymous JWT embedded in every page at
`__NEXT_DATA__.props.pageProps.environment.gasToken`. The **website** host is
behind Cloudflare and rejects every non-browser client regardless of headers —
it fingerprints the TLS handshake, so spoofing a User-Agent achieves nothing.
The **API** host is not protected. So Playwright drives real Chrome once per
token lifetime to mint the token, and every request after that is plain HTTP.

### Rate limits

Measured, not guessed: a rolling window of **41 requests**, then HTTP 429 for
about **45 seconds**. The limiter cares about volume per window, not
instantaneous rate — 0.9 req/s and 1.5 req/s hit the wall at the same request
number — and 3 concurrent requests trip a separate burst rule immediately. The
client paces at 2 in flight, 35 per minute, and waits out any 429 it does earn.
A full 78-session sweep takes 2–4 minutes.

## Failing loudly

"No seats" is the correct answer most of the time here, so a broken scraper
looks exactly like a working one. Everything below therefore alerts rather than
returning empty:

- token minting failure, or a 401 that survives a refresh
- more than 20% of session lookups failing — reports the failure instead of a partial picture
- **schema drift**: film id gone, zero screening dates, unexpected layout or seat
  count, unrecognised seat type or status
- a stale heartbeat, caught by the separate watchdog process

Repeat failures re-alert at most hourly, recovery is announced explicitly, and a
daily check-in confirms the monitor is alive so silence is never ambiguous. If
Telegram itself is unreachable, it falls back to a macOS notification and
`data/errors.log`, then reports the gap once it recovers.

Credentials are read at runtime from `~/.claude/channels/telegram/` and never
enter this repo.
