# The MCP server

Extant over the Model Context Protocol, revision **2026-07-28**, as six tools and two
resources on the configured build. The server holds no agent logic: each tool validates its
input, calls one function in the layers below, and shapes the result
([`backend.py`](backend.py) lists every function it may call).

```bash
uv run python -m extant.mcp_server                                   # stdio
uv run python -m extant.mcp_server --transport streamable-http --port 8765   # http://127.0.0.1:8765/mcp
```

It reads the same `.env` as the rest of Extant, connects as the read only role, checks
readiness before it accepts anything (a build that may not be served stops the process), and
counts `ask` against the same daily cap in Postgres. Over stdio the request log goes to
stderr, because stdout is the protocol channel.

## The verification boundary

`ask` is the only tool whose output is verified: every claim cites paragraphs, every quote is
checked verbatim against the paragraph it cites, every number against its quotes, and when
that cannot be done the result is a refusal with a reason. Every other tool returns records as
the index holds them, and says so twice: in its description (`UNVERIFIED`) and in its result
(`"verified": false` and a `verification` sentence). A client whose own model writes prose
from `search` results is outside the boundary: Extant never sees that prose and cannot check
it. The server's `instructions` tell a client the same.

## Tools

| Tool | Wraps | Filters |
|---|---|---|
| `ask` | `Service.ask` (the Phase 4 graph behind the Phase 5 guards); returns the Phase 4 `AskResponse` unchanged | the agent's own: entity optional (the question may name it; scopes that disagree are refused `entity_required`), date defaults to today with the `as_of_assumed` caveat |
| `search` | `PgIndex.search`, validity fixed to in force, trust filter on; query redacted before it is embedded | entity required, date (default today in India) |
| `fetch_paragraph` | `catalog.fetch_paragraph` | a lookup by id: returns the paragraph's scope and validity so the caller can check them |
| `resolve_circular` | `catalog.resolve_reference` (retrieval's reference path, no model, no embedding) | entity required for the citing paragraphs; a withdrawal holds for every entity |
| `diff_versions` | `catalog.diff_versions` | a lookup by direction between two dates |
| `list_applicable` | `catalog.directions_in_force` | entity required, date (default today in India) |

There is no parameter anywhere that switches off the entity or the date filter, reads
untrusted rows, or asks for paragraphs out of force; `tests/unit/test_mcp_server.py` holds the
allowlist of every input any tool accepts. Every result carries `index_version`, and every
filtered result reports the filters it applied, with `as_of_defaulted` when the date was not
given.

Entity codes match exactly, as they do in retrieval and in the agent: an NBFC subtype such as
`nbfc_ul` does not include paragraphs tagged only `nbfc` (none of the 610 `nbfc_ul` chunks
carries `nbfc`; 10,319 chunks carry `nbfc`). The tool descriptions say to call once with each.

## Resources

- `corpus://manifest`: the build being served, how it was built, and its coverage (313
  directions, 630 versions, 30 directions with a version RBI published only as a PDF).
- `corpus://entity_types`: every code, its label, and the one hierarchy (`nbfc_*` under `nbfc`).

## Version history and `diff_versions`

The index holds every consolidated direction from November 2025 version by version: 174 of 313
directions have more than one version (up to six), 317 supersedes links. It does not hold a
version RBI published only as a PDF, so 30 directions skip a version number. `diff_versions`
reports `changed`, `unchanged` (the same version was in force on both dates, or two versions
with identical text) or `not_covered` (a date before the first version the index holds), and
its message names any missing version in or next to the span. It never returns an empty
change set for a span it cannot see.

## Stateless

Revision 2026-07-28 has no session and no initialize handshake: every request is one POST
carrying its own protocol version, client capabilities and client info in `_meta`, with
`MCP-Protocol-Version` and `Mcp-Method` headers that must agree with the body (a mismatch is a
400 with code -32020). The server keeps nothing between requests either: it opens a read only
connection per call and closes it, and runs streamable HTTP with `stateless_http=True`, so a
client still on an older revision (one that performs the handshake, such as MCP Inspector
2.16) is served without a session too. Any number of replicas can serve the same clients.

## Cache hints

| Result | `ttlMs` | `cacheScope` | Why |
|---|---|---|---|
| `server/discover`, `tools/list`, `prompts/list`, `resources/list`, `resources/templates/list` | 86,400,000 (a day) | public | the same for every client (no per client state or authorisation); they change only when this code is deployed, which restarts the server. A stale list after a deploy costs one failed call and a refetch. |
| `resources/read` | 900,000 (15 minutes) | public | names the build being served, which changes when a new build is rolled out. Fifteen minutes bounds how long a client may show the old one, and every tool result carries its own `index_version`. |

Subscriptions are off (`subscriptions/listen` is not offered): nothing here changes while the
server runs.

## SDK

`mcp==2.3.0`, pinned exactly: the current release (2 October 2026), on the 2.x line that
first implemented revision 2026-07-28 (2.0.0, 28 July 2026). 1.x is maintenance only and
speaks the handshake revisions. The exact pin matters because the 2026-07-28 support is new:
in 2.3.0 the HTTP manager still decides a request's revision from the header alone (a `TODO`
in the SDK), behaviour the transport tests here depend on.
