# LinkedIn post

Short feed post. Its only job is to earn the click through to the article at
<https://ryzzuh.github.io/argos/> — so it lands the hook and the three most
surprising findings, and stops.

Paste as plain text; LinkedIn renders no markdown. Keep the blank lines.

---

## Draft

I've been using some downtime between roles to learn agentic development.

I'm obsessed with improving UX and have a soft spot for seeing epics at the
cinema. So naturally I built a monitoring system to buy two tickets to the
famously popular Odyssey.

It's screening in IMAX 70mm in Melbourne. A second round of showings was
released a few weeks back. Cracking my knuckles, I eagerly awaited the release.
As expected, the site became bogged down. By the time it wasn't, I couldn't find
a single session with a ticket that wasn't in the front-row corner.

What I didn't know was that seats come back. Returns, expired holds, failed
payments — continuously, invisibly, at no particular time of day. There is no
waitlist and no notification of any kind. In four days the monitor found 43 of
them. Nearly nine in ten lasted under an hour.

A few other things that turned up:

— The site's own sold-out flag is wrong on 34 of the 77 sessions.
— The back half of the cinema never released a single seat. Not one.
— "No results" and "broken" produce identical output, which turns out to be the
actual engineering problem.

It watches and sends me a link. A human does the buying.

Was this effort a success? Yes. I got E23 — row five, dead centre. Not the best
seat in the house, but the best one that came free in four days.

I've open-sourced it, so feel free to read more or run it yourself.

Write-up: https://ryzzuh.github.io/argos/
Code: https://github.com/ryzzuh/argos

---

**≈230 words.** The hook is the first two lines — between roles, learning
agentic development, straight into "so naturally I built a monitoring system to
buy two tickets" — which is the whole post in miniature and survives the feed's
"see more" fold.

Links sit at the very end, after the payoff. Putting them before it buries the
result and makes the closing line read as a repeat of the Code link.

## Image

Attach `docs/seat-map.jpg` — the near-solid grey seat map with the single blue
seat. It carries the argument without a caption, and it is the reason to stop
scrolling.

## Notes

- **Don't restate the article.** The three findings are bait, not a summary. The
  rate limits, the Cloudflare finding and the failure-design section are all
  reasons to click through, and none of them belong here.
- **The "a human does the buying" line stays.** It is a scraper pointed at a
  ticketing site for a film with a known reseller problem; the boundary is worth
  stating even in 200 words. The longer position is in the article.
- **Figures drift.** Re-run `uv run python -m argos.stats` before posting and
  correct the numbers if the monitor has been running.
