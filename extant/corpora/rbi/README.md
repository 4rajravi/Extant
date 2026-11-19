# RBI corpus adapter

Phase 1 of the cached build graph for the Reserve Bank of India Master Directions: crawl,
extract, parse, mine and the coverage report. The corpus is every direction listed by the
Department of Regulation (249) and the Department of Supervision (64), with all their published
versions (decided 19 November 2026): 313 directions, 668 versions.

```bash
uv run python -m extant.corpora.rbi crawl      # step 1, the only step that touches the network
uv run python -m extant.corpora.rbi extract    # step 2, offline from the cache
uv run python -m extant.corpora.rbi parse      # step 3, paragraphs
uv run python -m extant.corpora.rbi mine       # step 4, labels, identities and versions
uv run python -m extant.corpora.rbi report     # the coverage report, the baseline, the label sets
```

`extract` and `parse` take `--sample N` for the first N documents. `parse` and `mine` each need
only `crawl` and `extract`; `report` reads the output of both. Every step after
`crawl` is offline, and each is cached on its inputs and its own version: an unchanged rerun does
no work, and bumping a version redoes that step and nothing upstream of it.

| Step | Cached on | Version now |
|---|---|---|
| crawl | URL, ETag / Last-Modified under `--force` | |
| extract | `source_sha256` + `HTML_EXTRACTOR_VERSION` | `html-3` |
| parse | `source_sha256` + extractor version + `PARSER_VERSION` | `p2` |
| mine | the cached pages + `MINER_VERSION` | `m1` |

**The HTML direction pages are the source of record** (decided 19 November 2026). Step 2
extracts them with `extract_html.py`; `extract.py` (PyMuPDF) handles any PDF a person imports,
for example 13159, the one direction whose page carries no text.

The crawl also fetches every earlier version a direction's history page lists
(`NotificationPreVersion.aspx?id=<n>&Histid=<h>`), and `extract` extracts them with the current
pages. `crawl --force` refetches cached URLs conditionally (ETag or Last-Modified).
`crawl --retry-blocked` contacts again a host that served a bot challenge.

## PDFs, downloaded by a person

`rbidocs.rbi.org.in`, which serves every PDF, answers automated clients with a CAPTCHA. Extant
never answers or works around one, so the PDFs are downloaded by a person in a browser and then
imported:

```bash
uv run python -m extant.corpora.rbi pdf-list                       # writes the page of links
open data/rbi/derived/pdfs_to_download.html                         # download them, keep the names
uv run python -m extant.corpora.rbi import-pdfs ~/Downloads/rbi-pdfs
uv run python -m extant.corpora.rbi extract
```

Both commands are offline. `import-pdfs` matches files to target URLs by file name (every
target name is unique; browser counters such as `204MD (1).PDF` are understood), refuses any
file without a `%PDF-` header, so a saved CAPTCHA page cannot get in, never overwrites
different cached bytes without `--replace`, and is safe to re-run with partial downloads.
`pdf-list` and every import rewrite `derived/pdfs_to_download.html` and `.txt` with what is
still missing. Imported bytes are then indistinguishable from fetched ones to every later step,
while the cache entry and the manifest record that a person downloaded them.

## Step 4, labels from the published lists

Decided 19 November 2026: gold labels come from RBI's published lists only.

```bash
uv run python -m extant.corpora.rbi mine     # offline; needs crawl and extract first
```

`lists.py` reads three tables verbatim from cached pages: the withdrawal list (RBI/2025-26/100),
the standalone list (retained circulars) and the October 2025 draft repeal list (a cross check,
never gold). `mine.py` keys every row on `canonical_ref` (in `extant/core/identity.py`) plus the
circular date, because a Master Direction is listed once per updated version, and also reads the
"List of circulars repealed" annexes in the currently listed directions. Output, under
`derived/labels/m<MINER_VERSION>/`:

| File | Contents |
|---|---|
| `withdrawals_supervision.jsonl` | the same for the Department of Supervision's list, withdrawn by RBI/DoS/2026-27/221 of July 31, 2026 |
| `withdrawals.jsonl` | the withdrawal gold set as Phase 0 `Withdrawal` records, one per circular: keyed by `withdrawal_key` (canonical reference, circular date, title), duplicates merged, only rows with a withdrawing instrument and dates |
| `withdrawn.jsonl` | every withdrawal row: raw and canonical reference, title, circular date, footnote marks, `announced_on`, `effective_on`, `withdrawn_by` and the basis for those dates |
| `standalone.jsonl` | retained circulars; a `*` row is retained only in part |
| `draft_repeal.jsonl` | the draft list |
| `repeal_annexes.jsonl` | annex rows, with the direction page id, its `source_sha256` and the block anchor as evidence |
| `annex_attachments.jsonl` | annexes a page links as a separate PDF, absent from the HTML source |
| `summary.json` | every count and cross check |

A reference that does not normalise is kept with `canonical_ref` null and counted, never dropped.

`mine` also writes `derived/identities.jsonl`, each listed direction's doc_id base from its RBI
number (`RBI/DoR/2025-26/204` is `rbi:md:2025:0204`): from the masthead, or, for a page with no
text, from the PDF file name serial and the listing date's financial year. See
`identifiers.py`. Withdrawal rows carry `withdrawn_by_doc_base` (`rbi:circ:2025:0100`) and annex
rows `direction_doc_id`.

`mine` also writes `derived/versions.jsonl`. Versions follow RBI's own history (decided 19
November 2026): a direction's version history page lists its earlier consolidated texts,
"Updated as on <date>", oldest first, and those are `v1` to `vN`; the current page is `v(N+1)`.
A direction with no history has one version, `v1`. Each version after the first supersedes the
one before. Every row records its `updated_as_on` date and where that came from (`version
history`, the current page's `current title`, the `listing title` when the page has no text, or
`none`). Histories that are out of order, or a current text older than its history, raise.

## Step 3, paragraphs

```bash
uv run python -m extant.corpora.rbi parse
```

`parse.py` is a state machine over a page's extracted blocks (regions: front matter, preamble,
body, closing, annex). It starts a paragraph at each printed paragraph number that continues the
numbering, folds sub-paragraphs, provisos, lists, tables and table notes into it, follows chapter
and section headings into `chapter` and `heading_path`, and places every other block in exactly
one bucket (`front`, `toc`, `heading`, `body`, `footnote`, `closing`, `furniture`, `empty`). The
buckets always add up to the page's block count; the report checks it for every version.

Each version gets a status: `parsed`, `parsed_with_warnings`, `no_body` (the page has no text),
`no_paragraphs`, or `failed`, a record with the reason and the anchor, never a crash. Warnings say
what the parser decided and where: `absorbed_numbered_block`, `chapter_introduction`,
`numbering_gap`, `text_between_paragraphs`, `no_title`. It also reads, from the page itself,
`issued_on` (masthead), `effective_from` and its basis (`dated`, `issue date`, `not dated` for "on
the day it is placed on the official website", `not stated`), "Updated as on", and applicability:
the entity codes the applicability paragraph brings into scope, less the ones it excludes ("other
than Small Finance Banks", "excluding ... RRBs", "not applicable to the following: (i) MGC ...").
An exclusion of some paragraphs only leaves the entity in scope.

Output, rebuilt each run from the cache, under `derived/parsed/`:

| File | Contents |
|---|---|
| `html-3-p2/<sha[:2]>/<source_sha256>.json` | the cached parse of one page |
| `paragraphs.jsonl` | every paragraph, a Phase 0 `Paragraph` with `source_anchor` set and `source_page` null |
| `documents.jsonl` | a Phase 0 `Document` per version with a title |
| `facets.jsonl` | `applies_to`, `listed_under`, `applicability_status`, `effective_basis`, `parse_status`, `title_source`, `updated_as_on`, `rbi_page_id` |
| `parse_report.jsonl` | one row per version: status, counts, warnings, dates, block accounting |

### The parser gate

`tests/fixtures/rbi-md-2025-0259-v1`, `-0175-v1` and `-0382-v1` hold three pages (short and
simple, one with no applicability paragraph, one with annexes), each with `source.html`,
`page.txt` (its blocks, numbered) and `EXPECTED.md`. A person fills `EXPECTED.md` with the counts
and the anchor list read from the page, never from the parser's output;
`tests/unit/test_parser_gate.py` fails while any `TODO_HUMAN` remains and then checks the parser
against exactly those values.

## The coverage report

```bash
uv run python -m extant.corpora.rbi report
```

Offline, from the cache and the derived files. It writes, and these are committed:

| File | Contents |
|---|---|
| `evals/coverage-rbi-<YYYYMMDD>.md` | the funnel, paragraphs and tokens, statuses, warnings, applicability, dates, labels, the go or no go line, cross checks, failures |
| `evals/coverage-rbi-<YYYYMMDD>.jsonl` | the report's rows: one per version, from `parse_report.jsonl` |
| `evals/baseline-counts.json` | `documents`, `paragraphs`, `concordance`, `withdrawals`, with tolerances 0.0, 0.02 and 0.05 and the code versions |
| `evals/labels/rbi/withdrawals.jsonl` | Department of Regulation withdrawals, `Withdrawal` records |
| `evals/labels/rbi/withdrawals_supervision.jsonl` | Department of Supervision withdrawals, `Withdrawal` records |
| `evals/labels/rbi/retained.jsonl` | circulars retained as standalone; a later withdrawal, where one exists, in `withdrawn_by_doc_id` and `withdrawn_on` |
| `evals/labels/rbi/supersession.jsonl` | `ConcordanceEntry` records from repeal annexes, `consolidated_into`, document level |
| `evals/labels/rbi/MANIFEST.json` | the sets and the extractor, parser and miner versions they come from |

`tests/unit/test_rbi_report.py` validates every committed label row against the Phase 0 models
and fails when the manifest or the baseline names a different code version from the code, so a
version bump without a rerun of `report` cannot be committed unnoticed.

## Paragraph anchors

Decided 19 November 2026; rules in `anchors.py`. A para_id is `<doc_id>#<anchor>`: `p<number>` by
the printed number (`p62A`, `p4.1`), `annex<n>.p<number>` inside an annex printed in the page,
`annex<n>` for an annex without numbered paragraphs, `schedule<n>` and `appendix<n>` likewise, and
`preamble` for text before paragraph 1. A paragraph takes in its sub-paragraphs, provisos, lists,
tables and the numbered notes under those tables.

## Entity codes

Decided 19 November 2026; taxonomy in `entities.py`. The 11 listing categories: `cb` Commercial
Banks, `sfb` Small Finance Banks, `pb` Payments Banks, `lab` Local Area Banks, `rrb` Regional Rural
Banks, `ucb` Urban Co-operative Banks, `rcb` Rural Co-operative Banks, `aifi` All India Financial
Institutions, `nbfc` Non-Banking Financial Companies, `arc` Asset Reconstruction Companies, `cic`
Credit Information Companies. NBFC subtypes named in the NBFC directions' text are `nbfc_*`
(`nbfc_ul`, `nbfc_icc`, `nbfc_cic` for Core Investment Companies, and so on, 17 in all). `mine`
writes `derived/entity_types.jsonl` and `derived/listed_under.jsonl` (each direction's listing
category) and fails if a listing header no longer matches its category.

## Settings

All read from the environment or `.env`; see `.env.example`.

| Variable | Meaning |
|---|---|
| `EXTANT_RBI_CRAWL_CONTACT` | Email or URL sent in the User-Agent. Required. |
| `EXTANT_RBI_DATA_DIR` | Cache root, default `data/rbi`. Git ignored. |
| `EXTANT_RBI_REQUEST_INTERVAL_SECONDS` | Gap between requests. 2 is the floor and cannot be lowered. |
| `EXTANT_RBI_TIMEOUT_SECONDS` | Per request timeout. |

## Politeness

One request at a time, at least two seconds between any two requests (each redirect hop
counts), exponential backoff on 5xx, 429 and transport errors, three attempts at most. Three
refusals in a row (401, 403, 418, 429) stop the crawl. A host that serves a bot challenge,
such as a CAPTCHA, is never answered or worked around: it is written to `blocked_hosts.json`
and skipped by every later run until `--retry-blocked`.

## Cache layout

Everything lives under `EXTANT_RBI_DATA_DIR`.

```
blobs/<sha[:2]>/<sha256>            raw response bytes, content addressed
responses/<url_key>.json            one cache entry per URL (url_key = sha256 of the URL)
manifest.jsonl                      append only, one row per fetch
blocked_hosts.json                  hosts that served a bot challenge, with when and where
derived/listing_rows.jsonl          rows of the entity category listings, rebuilt by crawl
derived/identities.jsonl            each listed direction's doc_id base and how it was derived
derived/versions.jsonl              every version: doc_id, date, URL, Histid, supersedes
derived/entity_types.jsonl          the 28 entity types, as Phase 0 EntityType rows
derived/listed_under.jsonl          each listed direction's category in RBI's listings
derived/pdf_targets.jsonl           every PDF the corpus needs: url, title, category, direction id
derived/pdfs_to_download.html|.txt  the PDF targets not cached yet
extract/html-<N>/<sha[:2]>/<source_sha256>.json            step 2 output, HTML
extract/v<EXTRACTOR_VERSION>/<sha[:2]>/<source_sha256>.json   step 2 output, PDF
derived/labels/m<MINER_VERSION>/                              step 4 output
derived/parsed/                                               step 3 output
```

Only 2xx responses become cache entries. A failure is in the manifest only, and the next
crawl retries it. A cache entry is never refetched without `--force`.

### `responses/<url_key>.json`

| Field | Meaning |
|---|---|
| `url` | The URL as requested; the cache key |
| `final_url` | After redirects |
| `status` | HTTP status of the final response; null for an imported file |
| `fetched_at` | UTC, ISO 8601, seconds |
| `source_sha256` | sha256 of the body, which names the blob |
| `bytes` | Body length |
| `content_type`, `etag`, `last_modified` | From the response headers |
| `headers` | All response headers, lowercased, except `set-cookie`; empty when imported |
| `origin` | `fetched` by the crawler, or `imported` from a person's download |
| `imported_file` | The file name an imported entry came from, else null |

### `manifest.jsonl`

One JSON object per line, one line per fetch. Cache hits are not fetches and write nothing.

| Field | Meaning |
|---|---|
| `url` | As requested |
| `fetched_at` | UTC timestamp |
| `outcome` | `fetched`, `not_modified` (304 under `--force`), `failed`, `blocked` (bot challenge), `invalidated` (entry removed by hand, with the reason in `error`), `imported` (a person's download, file name in `error`) |
| `status` | Final HTTP status, or null after transport errors |
| `etag` | As served |
| `bytes` | Body length |
| `source_sha256` | sha256 of the body when it was cached, else null |
| `attempts` | 1 to 3; 0 for an import |
| `final_url`, `content_type`, `error` | As available |

### Step 2 output, HTML (the source of record)

`extract/html-3/<sha[:2]>/<source_sha256>.json` per direction page
(`BS_ViewMasDirections.aspx?id=<n>`): `source_sha256`, `extractor_version`, `url`, `status`
(`extracted`, `no_body` when the page has no `table.td` text, or `failed`), `error`, and `blocks`
in document order. Each block has:

| Field | Meaning |
|---|---|
| `index` | Position in the page, from 0 |
| `kind` | `heading`, `paragraph`, `footnote`, `list_item`, `table` or `text`; from markup only, and RBI's markup is inconsistent, so treat it as a hint |
| `tag`, `css_class` | The element, verbatim |
| `text` | As rendered: ASCII whitespace collapsed, `<br>` as a newline, non-breaking spaces kept, footnote markers left in place |
| `markers` | Each `<sup>`: `start`, `end` (offsets into `text`), `text`, `href` (`#F9`), `title` (often the amendment note with its effective date) |
| `ids` | `id` and `name` attributes inside the block, the targets of the page's own links |
| `list_depth`, `list_type`, `list_ordinal`, `list_marker` | For list items: the numbering a browser draws, which is not in the text |
| `rows` | For tables: every cell's text, row by row; a nested table's text is folded into its cell |

### Step 2 output, PDF

`extract/v<N>/.../<source_sha256>.json` holds `source_sha256`, `extractor_version`, `url`,
`status` (`extracted`, `partial` or `failed`), `page_count`, `failures` (each with a 1 based
`page`, or null when the file would not open) and `pages`. Each page has its `number`,
`width`, `height`, plain `text` in PyMuPDF reading order, and `blocks`, then `lines`, then
`spans` with `bbox`, `font`, `size` and `flags`. Bytes without a `%PDF-` header are refused,
because PyMuPDF will otherwise open an HTML challenge page as if it were a PDF.

## Layer boundaries

Enforced by `tests/unit/test_rbi_boundaries.py`.

- `acquisition.py` and `importer.py` own bytes and the manifest and import no HTML or PDF
  library; `importer.py` is also offline.
- `sources.py` and `crawl.py` discover links in cached HTML and never extract PDFs.
- `extract.py`, `extract_html.py`, `lists.py`, `mine.py`, `identifiers.py`, `entities.py`,
  `anchors.py`, `parse.py`, `parse_run.py` and `report.py` are offline and import no HTTP
  client.
- Nothing under `corpora/rbi/` imports a model or embedding client.
