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

Here's the thing I didn't know: seats come back. I'd assumed a sold-out session
was sold out — that if someone couldn't make it, their ticket quietly evaporated.
It doesn't. Returns and expired holds go back on sale continuously. Nobody tells
you when. There's no waitlist, no alert, no "let me know if something frees up".
There's a watchlist, and it saves the film, not the seats.

The Odyssey is screening in IMAX 70mm in Melbourne and it's sold out — except the
site lists sessions as available anyway, and when you open one the seat map looks
empty.

It isn't quite empty. It's showing you whatever nobody wanted: a front-row corner
seat, or one of the six accessibility spaces, which aren't general admission. Of
the 42 bookable seats this thing has caught in four days, 28 were in the front
two rows and none were further back than row F. Meanwhile the booking API's own
`isSoldOut` flag reports 34 of those 77 sessions as perfectly fine.

The listing tells you none of it. Session times and nothing else — no seat
counts, no filter for row or seat type, no way to ask which sessions have two
seats together. Finding out means opening 77 seat maps and squinting.

So: ~2,000 lines of Python that ignores the flag, counts the actual seat map by
seat type, and messages me when a seat a human can actually book appears.

It doesn't book anything. It sends me a link and I do the rest — which, on
current evidence, gives me about forty minutes to act.

123 sweeps so far. 91 of them found nothing. That ratio is the entire argument
for automating it.

It's caught three genuine releases. The longest any of them survived was under an
hour.

To be clear about what this is: read-only, paced well under the rate limit, one
person buying two tickets. I don't condone or support using it — or anything like
it — to bulk-grab inventory or resell. If that's your plan, this isn't for you
and I'd rather you didn't.

Mostly I built it for me. But it's public now, because the gap it fills isn't
mine — anyone chasing this season is squinting at the same 77 seat maps, and
most of them don't know the seats come back at all.

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

## Claims to keep accurate

Every factual claim below was verified before drafting, because the post names a
real business:

- **34 of 77 sessions report `isSoldOut: false`** while having nothing bookable —
  measured across the sweep history, not estimated.
- **Screen 1 has exactly 4 wheelchair and 2 companion seats**, from the seat
  layout endpoint. Mentioned as one category of unwanted seat, not as the story —
  the point is which seats go unsold, not who they are reserved for.
- **42 distinct bookable seats found over four days; 28 in rows A-B, none past
  row F.** Straight from the `free_seats` table, deduplicated by seat id.
- **Returns and expired holds do go back on sale**, which is the finding that
  started the whole thing and is not communicated anywhere in the booking flow.
- **The listing shows times only** — no seat counts, no filters. Verified on the
  film page.
- **"Add to watchlist" saves the film, not seat availability.** The page carries
  no "notify", "alert", "remind" or "waitlist" language anywhere.

Not claimed, because it could not be verified: anything about filtering *inside*
the seat picker, which sits behind sign-in and was not inspected.

## Two things the post must not do

- **Overclaim reusability.** It monitors one film at one cinema. The README is
  explicit about that and the post should not undo it.
- **Leave the scalping question hanging.** This film is a heavy reseller target —
  IMAX's own page warns about it — so a public tool watching a sold-out season
  can be misread by anyone skimming. The draft answers it twice, deliberately:
  once as a fact ("it doesn't book anything") and once as a position ("I don't
  condone or support..."). The fact alone reads as a technical footnote; the
  position alone reads as boilerplate. Both, and it lands.

The tone note: the disclaimer is placed *after* the punchline about forty
minutes, not before it. A caveat that opens a paragraph kills the deadpan; one
that follows a joke reads as the author being straight with you.
