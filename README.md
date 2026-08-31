# Argos

> Odysseus's dog, who waited twenty years on a dung heap and was the only one to
> know his master the instant he walked through the gate. Also Argus Panoptes,
> the hundred-eyed watchman who never closed every eye at once — though Hermes
> did eventually kill him by lulling him to sleep, which is precisely this
> tool's failure mode when the laptop shuts.

Watches seat availability for **THE ODYSSEY — IMAX 70MM PRESENTATION** at IMAX
Melbourne and pushes a Telegram alert the moment a genuinely bookable seat is
released.

**It alerts only.** It never selects a seat, creates an order, holds anything, or
buys anything — you complete checkout yourself from the link in the alert.

## What this actually is

A worked example, not a product. It monitors **one film at one cinema**, and the
config carries that cinema's hostnames, its auditorium's seat count and its
screen's layout id. The *technique* generalises to any site running Vista's
Movie XChange platform — which is a lot of cinema chains — but only IMAX
Melbourne has been tested, and the code does not pretend otherwise.

The parts worth stealing are in [How it gets the data](#how-it-gets-the-data)
and [Failing loudly](#failing-loudly): finding the JSON API behind a booking
site, pacing against a rate limiter you have measured rather than guessed, and
building a monitor whose failure mode is not silence.

## Three things that make this film hard to book

**The "available" seats are usually wheelchair spaces.** Screen 1 seats 459: 428
Normal/Standard, 25 Normal/Premium, 4 Wheelchair and 2 Companion. Those last six
are free on nearly every session and are not ordinary tickets — which is why a
seat map can look empty on a session the site lists as available, and why
alerting on them would fire every single run and mean nothing.

**The `isSoldOut` flag disagrees with the seat map.** Around 34 of 77 sessions
report `isSoldOut: false` while having nothing bookable on them. Every number
here is counted from the seat map and split by seat type instead.

**Nothing in the UI answers the question you actually have.** The listing gives
session times and nothing else — no seat counts, no filter for row or seat type,
no way to ask which sessions have two seats together. Answering that by hand
means opening every session's seat map in turn. And there is no availability
notification: the site has a watchlist, but it saves the film, not the seats.

> **Practical note on film ids.** The id is the only thing that identifies a
> presentation; the slug in the URL is decorative and ignored. `HO00000547` is
> the IMAX 70mm presentation and `HO00000546` is the 4K Laser one — different
> films for booking purposes. An id with no sessions renders an empty date
> picker rather than an error, so check the id before concluding a season is
> sold out.

## Requirements

- **macOS.** Scheduling uses `launchd`. The scraper itself is portable; the
  agents are not.
- **Google Chrome installed.** Playwright drives your real Chrome via
  `channel="chrome"`. Playwright ships no bundled Chromium for macOS 13, so on
  older macOS this is not optional.
- **[uv](https://docs.astral.sh/uv/)** and Python 3.11+.
- **A Telegram bot** (free, two minutes — below).

## Setup

```bash
git clone https://github.com/<you>/argos.git
cd argos
uv sync
```

### Create a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`, and
   follow the prompts. It replies with a token like `1234567890:AA...`.
2. Send your new bot any message (it cannot message you first).
3. Get your numeric chat id:
   ```bash
   curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates" | grep -o '"id":[0-9]*' | head -1
   ```
4. Export both. Put them in your shell profile so the scheduled agents inherit
   them:
   ```bash
   export TELEGRAM_BOT_TOKEN="1234567890:AA..."
   export TELEGRAM_CHAT_ID="987654321"
   ```

Confirm delivery before relying on it:

```bash
uv run python -m argos.notify        # sends a test message
```

### Run it once

```bash
uv run python -m argos.token         # mint a token (prints expiry only, never the token)
uv run python -m argos.watch         # full sweep, diff, alert, render the report
```

Expect a full sweep to take 2–4 minutes: the API allows 41 requests per rolling
minute and the client stays under it deliberately. You should end up with
`REPORT.md` and `docs/index.html`.

## Commands

```bash
uv run python -m argos.token         # mint/refresh the bearer token
uv run python -m argos.scrape        # one sweep -> SQLite
uv run python -m argos.report        # REPORT.md + docs/index.html
uv run python -m argos.watch         # sweep, diff, alert, re-render
uv run python -m argos.health        # watchdog: is the monitor alive?
uv run python -m argos.cloud         # stateless variant (see the cloud section)
```

| Flag | Effect |
|---|---|
| `--within-days N` | only check sessions N days out (a watchlist run) |
| `--dry-run` | sweep and diff, send nothing |
| `--replay N` | re-send snapshot N's alert, clearly marked as a test |
| `--no-report` | skip re-rendering the page |
| `--film-id ID` | point at a different film |

## Scheduling

```bash
./bin/install-agents.sh              # generate plists for this checkout and load them
./bin/install-agents.sh --dry-run    # print what it would write
./bin/install-agents.sh --uninstall  # unload and remove
```

The plists cannot be committed with real paths, so they are generated from
`launchd/com.argos.plist.template` using wherever you cloned the repo.

| Job | Every | Does |
|---|---|---|
| `watch` | 10 min | next 14 days — the sniping loop |
| `full` | 3 h | every session, and re-renders the report page |
| `watchdog` | 2 h | alerts if no sweep has run in 90 min |

The watchdog is deliberately a separate process, because the failure it exists to
catch is the other two not running at all.

Check with `launchctl list | grep argos`; logs land in `data/*.log`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | bot token; falls back to Claude Code's Telegram channel config if present |
| `TELEGRAM_CHAT_ID` | — | numeric chat id to alert |
| `IMAX_FILM_ID` | `HO00000547` | which film to watch |
| `IMAX_DATA_DIR` | `./data` | scratch: token cache, SQLite, logs |
| `IMAX_STATE_DIR` | = data dir | durable state: heartbeat, alert dedupe, seat baseline |
| `IMAX_BROWSER_CHANNEL` | `chrome` | empty string uses Playwright's bundled Chromium |
| `IMAX_SOURCE` | hostname | label appended to every alert, so you can tell machines apart |

## Pointing it at something else

Set `IMAX_FILM_ID`, or pass `--film-id`. To find one:

```bash
uv run python -c "
import asyncio; from argos.api import Ocapi
async def main():
    async with Ocapi() as api:
        for f in await api.films(): print(f['id'], f['title']['text'])
asyncio.run(main())"
```

Three constants in `argos/config.py` are specific to this film and auditorium and
will produce drift warnings until you update them: `FILM_SLUG`,
`EXPECTED_LAYOUT_ID` and `EXPECTED_SEAT_COUNT`. That is the intended behaviour —
see below — but it is noisy if you ignore it.

For a **different cinema**, `WEB_BASE` and `API_BASE` need changing too. Other
Vista deployments very likely expose the same OCAPI shape, but none have been
tested, so treat that as a starting point rather than a promise.

## What triggers an alert

1. **A seat is released** — a `Normal`-type seat that was not free on the
   previous sweep.
2. **A new screening is added** — a session that has just appeared with seats
   free. This needs its own detector: a session that did not exist before has no
   previous state to differ from, so from the second sweep onward the seat diff
   considers all its seats "unchanged" and says nothing. On a sold-out run, an
   added screening is the likeliest way a ticket becomes available at all.

A new screening *date* is caught on the next sweep, because the date list is the
schedule cache's key. A new screening on an existing date is caught by the next
full sweep, which re-reads the schedule outright.

## How it gets the data

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
`__NEXT_DATA__.props.pageProps.environment.gasToken`. The **website** host sits
behind Cloudflare and rejects every non-browser client regardless of headers — it
fingerprints the TLS handshake, so spoofing a User-Agent achieves nothing. The
**API** host is not protected. So Playwright drives real Chrome once per token
lifetime, and every request after that is plain HTTP.

### Rate limits

Measured, not guessed: a rolling window of **41 requests**, then HTTP 429 for
about **45 seconds**. The limiter cares about volume per window rather than
instantaneous rate — 0.9 req/s and 1.5 req/s hit the wall at the same request
number — and 3 concurrent requests trip a separate burst rule immediately. The
client paces at 2 in flight, 35 per window, and waits out any 429 it does earn.

## Failing loudly

"No seats" is the correct answer most of the time here, so a broken scraper
looks exactly like a working one. Everything below therefore alerts rather than
quietly returning nothing:

- token minting failure, or a 401 that survives a refresh
- **schema drift** — film id gone, zero screening dates, unexpected layout or
  seat count, unrecognised seat type or status
- **coverage loss** — more than 10% of sessions unchecked; above 20% the sweep
  refuses to report a partial picture at all
- a stale *or persistently failing* heartbeat, caught by the separate watchdog

A single failed lookup is logged, not alerted: it is a network blip, and paging
someone about it teaches them to ignore the channel that also carries the seat
alerts. Repeat failures re-alert at most hourly, recovery is announced
explicitly, and a daily check-in confirms the monitor is alive so silence is
never ambiguous. If Telegram itself is unreachable it falls back to a macOS
notification and `data/errors.log`, then reports the gap once it recovers.

## Why this does not run in the cloud

It was tried and it does not work. `.github/workflows/watch.yml` is kept with its
schedule disabled so the finding stays reproducible via **Run workflow**.

A GitHub runner is served a Cloudflare challenge instead of the page, so there is
no token to extract:

```
headless: page loaded but no gasToken in __NEXT_DATA__ (likely a Cloudflare challenge)
```

The runner used real headless Chrome with a genuine browser TLS fingerprint, so
this is **IP reputation, not client detection** — no VPS will fix it either. Only
a residential connection gets through. `argos/cloud.py` and the
`state/seats.json` diff baseline still work and are exercised locally; they would
come alive on any always-on machine at home.

The practical consequence: **while the machine is asleep or off, nothing is
watched.** launchd agents only run when the machine is awake and logged in, and
on wake launchd fires a single catch-up run rather than one per missed interval.
That gap is an accepted trade-off here, not an oversight. The watchdog reports it
afterwards, so expect a "no sweep for N minutes" message most mornings — it is
telling you exactly which window went unwatched.

## Conduct

Read-only `GET`s against a public booking API, paced below a rate limit that was
measured rather than assumed, for one person buying one pair of tickets. It
creates no orders, holds no seats, and completes no purchases. Roughly comparable
to leaving the page open and refreshing it — which is what it replaces.

Credentials are read at runtime from environment variables (or, on the author's
machine, Claude Code's Telegram channel config) and never enter this repo.

## Licence

MIT — see [LICENSE](LICENSE).
