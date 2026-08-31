# LinkedIn post — drafts

Voice: wry and deadpan. The joke is the disproportion between the engineering and
the goal, delivered flat. Understatement only works when the detail underneath it
is real, so every number below is measured, not rounded up for effect.

Target ~250 words: long enough to land three findings, short enough that the feed
does not truncate the payoff.

---

## Main draft

I spent a weekend building a monitoring system with a rate limiter, a dead-man's
switch and a Telegram bot, so that I could buy two cinema tickets.

In my defence, it was necessary.

The Odyssey is screening in IMAX 70mm in Melbourne. It's sold out. I couldn't
find a single seat — which I assumed was the film's fault, and turned out to be
mine. The URL I'd been given pointed at the wrong film id. The slug in the
address bar is decorative; only the id counts, and mine belonged to a different
film with no sessions at all. The site doesn't error on that. It renders an empty
date picker, which looks exactly like "sold out".

Then it got worse. The booking API has an `isSoldOut` flag. It's wrong on 34 of
77 sessions. And the seats it does report as available are almost always the four
wheelchair spaces and two companion seats in row M — not tickets I can buy. So
the seat map reads as empty on sessions the site insists are open.

So: ~2,000 lines of Python that ignores the flag, counts the actual seat map, and
messages me when a real seat appears.

123 sweeps so far. 91 of them found nothing. That ratio is the entire argument
for automating it.

It's caught three genuine releases. The longest any of them survived was under an
hour.

The cinema code isn't the reusable part. The reusable part is that "no results"
and "broken" look identical from the outside — so the thing has to be able to
tell you which one it's having.

Repo: <REPO_URL>

---

## Alternate hook A — numbers first

> 123 automated checks. 91 of them found absolutely nothing.
>
> This is a success story.

Then straight into "The Odyssey is screening in IMAX 70mm…". Front-loads the
counterintuitive line, which tends to survive the feed's truncation better.

## Alternate hook B — flattest possible

> My seat-availability monitor has a dead-man's switch, a schema-drift detector
> and an hourly alert-deduplication policy.
>
> It monitors cinema tickets.

Strongest deadpan, weakest context — works if your audience already knows you
build things, less so if the post is reaching strangers.

---

## Image

Posts carrying one image travel materially further. Use a dark-mode screenshot of
the report page showing a live hit — the green "2 seats free right now" verdict
block with the seat labels and session time visible.

`docs/index.html` renders it. If the current sweep is empty, regenerate from a
snapshot that had a hit:

```bash
uv run python -c "
from argos import store
for s in store.latest_snapshots(40):
    if s['bookable_total']: print(s['id'], s['taken_at'], s['bookable_total'])"
```

Crop to the header and verdict block. Don't include the full session table — it's
unreadable at feed size.

## Two things the post must not do

- **Overclaim reusability.** It monitors one film at one cinema. The README is
  explicit about that and the post should not undo it.
- **Imply it books anything.** It alerts; a human buys. Worth being precise about
  given it's a scraper pointed at a ticketing site.
