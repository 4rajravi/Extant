# The page

One static page served by the API at `/`: [`index.html`](index.html), [`app.css`](app.css)
and [`app.js`](app.js). Vanilla JavaScript, no framework, no bundler, no build step; the files
are served as written. The policy is `default-src 'self'`: no fonts, scripts or frames from
anywhere else, so the type is the system's (a book face for provision text, the system sans
for everything else, mono for identifiers).

```bash
uv run python -m extant.api --port 8080     # then open http://localhost:8080
```

## What it shows

- **The question**, with an entity selector filled from `GET /entity_types` and an as of date.
  Both default to nothing, so the graph's own `entity_required` and `as_of_assumed` behave as
  designed.
- **Working**: the graph's progress from `POST /ask/stream`, one row per node as it starts,
  completed with what the node wrote (route, paragraphs retrieved and held, rows set aside,
  claims drafted and verified, the checks recorded so far) and the node's own `latency_ms`.
- **The answer**, once, verified. Each citation expands to its paragraph from
  `GET /paragraph/{para_id}`, with the quoted span highlighted inside the full text, and the
  instrument, paragraph number, dates, entity scope, binding flag, authority rank and a link
  to the source.
- **What the answer rests on**: every row the gate set aside, with reason and detail, and every
  paragraph retrieved with its scores, in retrieval's order. Visible with the answer, without
  any interaction.
- **Caveats and degradation** inside the answer sheet. A degraded answer is drawn thinner (a
  dashed sheet, quieter claim text) rather than identical with a note.
- **The notice** under the masthead: not legal or tax advice.
- **Examples** from `GET /examples`: one click, no model call, and they work with the database
  down. A stale example says which build answered it.

A refusal is a calm, differently tinted sheet: the message as prose, `nearest` as "Found, but
not relied on" with the same citation affordances, the reason code small and in mono.
`cap_reached` offers the cached examples.

## The boundary

The page reads the response as given and computes no fact. Concretely: dates are shown as the
API sends them (`2026-03-10`, never a local format); whether something is in force is never
recomputed (a null end date reads "no end date in this build"); a quote is never truncated;
nothing is sorted; and no count is derived (the gate with nothing dropped says "nothing set
aside", not "0 dropped"). Scores are shown with the precision the API sends.

Labels are the page's own copy: names for nodes, refusal reasons, caveats and drop reasons.
Each code is shown beside its label.

### Highlighting a quote

`quote_start` and `quote_end` are offsets into the paragraph body in characters (Python code
points), so the page slices the body by code point, not by JavaScript's UTF-16 units. The span
is highlighted only when all of these hold; otherwise the paragraph is shown whole and the
reason is stated:

- the offsets fall inside the body (`0 <= start < end <= length`);
- the paragraph was not reached through an alias (the offsets belong to the retired id's
  text; the alias note from the API is shown instead);
- the paragraph comes from the same build as the response that cited it (a cached example
  answered on an older build would otherwise be highlighted against text that may have
  changed).

## Tests

`tests/web/`, marked `web`, in Playwright's Chromium against the real app on a replaying
runtime (no database, no model). `uv run playwright install chromium`, then
`uv run pytest -m web`. CI runs them.

- the gate: a question answered with citations, one expanded with its quoted span highlighted
  exactly; an example opened with no request to `/ask` and no model call;
- each refusal class renders as an outcome, never an error;
- the dropped list and the retrieved list render without interaction, in the response's order;
- the progress panel moves only when an event arrives (a gated runtime holds each node open);
- no date in the DOM differs from a date the API returned;
- offsets outside the paragraph, and a citation through an alias, are not highlighted;
- nothing moves when the answer arrives, at 390 and 1280 pixels wide, and the page never
  scrolls sideways.

## The manual check, 24 November 2026

Against the real API on local Postgres, real models, build `idx_rbi_20261123_c23c1e8`.

- **The wait.** Full path questions took 10 to 15 seconds. Most of that is one step: drafting
  claims (`compose`, one model call) took 3.5, 8.3, 8.9 and 10.9 seconds across four questions,
  after 1.3 to 2.1 seconds reading the question and 0.4 to 3 seconds searching. The panel
  is honest throughout. For the first five seconds it reads as rigour: the route, the entity,
  "as of 2026-11-24 (today)", ten paragraphs retrieved, four superseded versions set aside by
  name. Then it sits on "Drafting claims from the paragraphs…" with nothing new for up to
  eleven seconds. That stretch reads closer to a hang than to rigour, though the pulsing mark
  and the completed rows above it make clear the page is alive. Then verification, the judge
  and the answer land within a second, and the checks list that appears there is the most
  convincing thing on the page.
- **The first request after a start** waited 1.5 s before the first node (the pool, the entity
  list and the model clients warming); the panel says only "Sent. Waiting for the first step."
  Later requests reached their first node in under 30 ms.
- **A refusal.** One real question was refused `no_support_found` for `nbfc_ul`, and one
  `verification_failed` (the draft wrote "15 %" where the quotes say "15 per cent", and the
  numbers check withheld the claim). Both read as careful, not broken: the message is the
  content and the nearest paragraphs are one click away.
- **Keyboard.** The whole page is reachable by Tab in reading order: skip link, question,
  entity, date (three stops: day, month, year), Ask, each example, each citation. Enter
  expands a citation; the focus ring is visible in both themes.
- **Light and dark, 390 and 1280 wide.** See `docs/screenshots/phase-9/`. A table of retrieved
  scores overflowed the page at first (the 17 digit scores widened the grid); it is now a list.

What still felt wrong:

- The long wait inside `compose`. The event stream cannot say more without the composer
  reporting progress of its own, which one structured model call does not.
- On a phone the checks list is long and sits above the answer, so reading the answer means
  scrolling past forty chips. Collapsing it when the answer arrives would move what the reader
  is looking at, so it is left open.
- The date input displays in the browser's locale (`dd/mm/yyyy` here) while every date the
  page shows is ISO. The value sent is ISO; only the native control's display differs.
