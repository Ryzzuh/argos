# LinkedIn post

Short feed post. Its only job is to earn the click through to the article at
<https://ryzzuh.github.io/argos/> — so it lands the hook and the three most
surprising findings, and stops.

Paste as plain text; LinkedIn renders no markdown. Keep the blank lines.

---

## Draft

I've been using some time between roles to learn agentic development properly —
not the demos, the actual thing. What breaks, what you have to design around,
where it genuinely saves you and where it quietly costs you.

So naturally I built a monitoring system to buy two cinema tickets.

The Odyssey is screening in IMAX 70mm in Melbourne. 459 seats, gone, session
after session, a month deep into the schedule.

Here's what I didn't know: seats come back. Returns, expired holds, failed
payments — continuously, invisibly, at no particular time of day. There is no
waitlist and no notification of any kind. In four days it found 43 of them. Most
lasted under an hour.

A few other things that turned up:

— The site's own sold-out flag is wrong on 34 of the 77 sessions.
— The back half of the cinema never released a single seat. Not one.
— "No results" and "broken" produce identical output, which turns out to be the
actual engineering problem.

It watches and sends me a link. A human does the buying.

Write-up: https://ryzzuh.github.io/argos/
Code: https://github.com/ryzzuh/argos

---

**≈200 words.** The hook is the first two lines — "learning agentic development"
into "so naturally I built a monitoring system to buy two cinema tickets" — which
is the whole post in miniature and survives the feed's "see more" fold.

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
