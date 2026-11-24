# The HTTP API

The second entry point to the same graph, after MCP, and the one the frontend consumes. It
owns transport and the operational guards and nothing else: every route validates, calls one
method of [`Runtime`](runtime.py), and shapes the result. No retrieval logic, no agent logic.

```bash
uv run python -m extant.api --port 8080        # PORT is read from the environment too
```

It starts whether or not Postgres is reachable, and reports whether it may take traffic on
its readiness probe. Uvicorn's own proxy header handling is off (see Rate limiting).

## Endpoints and status codes

| Route | Success | Other statuses |
|---|---|---|
| `POST /ask` | **200** with the Phase 4 `AskResponse`, unchanged, whatever the outcome: `answer`, `refusal`, `retrieval_only` or `unavailable` | **429** rate limited, no answer body · **422** invalid request |
| `POST /ask/stream` | **200**, server sent events: the graph's progress, then the same `AskResponse` once (see below) | **429** · **422**, as `POST /ask`, before the stream starts |
| `GET /paragraph/{para_id}` | **200** with the paragraph; `resolved_through_alias` and a `note` when the id was retired | **404** neither a paragraph nor an alias · **503** index unreachable · **422** not a paragraph id |
| `GET /examples` | **200** with the cached answers, the build they came from and `stale` | none: it touches neither the database nor a model |
| `GET /entity_types` | **200** with the build's entity taxonomy, for the page's selector | **503** index unreachable |
| `GET /` | **200**, the page (`extant/web`), with a self only Content-Security-Policy | none |
| `GET /health` | **200** ready | **503** not ready (manifest check failed, or the database unreachable) |
| `GET /health/live` | **200** | none: it touches nothing |

`para_id` contains `#`, so it is sent as `%23`: `/paragraph/rbi:md:2025:0143:v1%23p12`.

**A refusal is an outcome, not an error.** `POST /ask` returns 200 with `result.outcome`
`refusal` when the system declines, with its reason and, for the reasons that have them, the
`nearest` provisions it would not stand behind. A client treats a careful refusal as a result.

Where that rule stops, as implemented:

- **`cap_reached` is 200**: a refusal with cached examples, plus `Retry-After` (an HTTP date)
  naming the next **midnight in India**, when the count resets. The service is still answering,
  differently: rung four of the degradation ladder.
- **Postgres unreachable is 200 too**: the `unavailable` outcome with cached examples, rung
  four again. The same reasoning as `cap_reached`, and a client handles both the same way.
- **A rate limit breach is 429**, with an error body (`detail`, `index_version`,
  `retry_after_seconds`) and `Retry-After` in seconds. Nothing was attempted; it is a
  rejection, not an outcome.

The OpenAPI document (`/openapi.json`, `/docs`) carries all of this: the 200 response with its
four outcomes and the `cap_reached` header, the 429 with its own body.

Every response names the build it came from: `index_version` in every body, errors included,
and the `X-Extant-Index-Version` header on every response. That is what makes an answer
reproducible months later.

`dropped` is part of the answer, not debug output: what the system saw and would not rely on
(a superseded version, another entity's provision) tells a compliance reader it was excluded
rather than missed. It is served on answers and refusals alike.

`GET /paragraph` and `GET /examples` send `Cache-Control: public, max-age=900`: a build is
immutable, so neither can change under it, and fifteen minutes bounds how long a client keeps
one after a new build. An answer is never cached.

## Streaming: `POST /ask/stream`

The same request as `POST /ask`, answered as server sent events, added in Phase 9 for the
page. `POST /ask` is unchanged, for MCP and any client that wants one payload.

| Event | When | Carries |
|---|---|---|
| `node_started` | a graph node begins | `node` |
| `node_finished` | that node has returned | `node`, `latency_ms`, `model_calls`, and what it wrote: the route, entity and date (parse), paragraphs held, rows `dropped`, claims drafted or verified, every check recorded so far, degradation markers, a refusal reason |
| `restarted` | the database failed and the service runs the graph once more | `attempt`, `reason` |
| `response` | last, once | `response` (the `AskResponse`, byte for byte what `POST /ask` returns) and `retry_after` (an HTTP date on `cap_reached`, which `POST /ask` sends as a header) |
| `failure` | last, instead of `response`, when the request failed after the stream began | `detail`, `index_version` |

The events are true by construction: the node wrapper (`extant.agent.runtime.run_node`) calls
an observer as each node starts and once it has returned, and nothing else emits one. A node
that takes eleven seconds is eleven seconds between its two events. Every count in an event is
counted in `extant.agent.progress`, so a client shows it as given. There is no token stream:
the answer text exists only in `response`, after verification.

The graph runs on a worker thread that writes events into a queue the response drains; it is
still one request, with nothing kept after it. A client that disconnects does not stop the
graph: the answer is computed and counted against the cap all the same. Proxies must not
buffer the response (`X-Accel-Buffering: no` is sent; Cloud Run streams, unverified until
Phase 10).

## Admission

Identifiers in a question (PAN, Aadhaar, GSTIN, account numbers, and the rest of
`extant/guards/pii.py`) are redacted by `Service.run`, the first thing `POST /ask` calls:
before the state object is built, before any embedding, model call or log line, so
`query_sha256` is over the redacted form and the raw question is never persisted. It lives in
the Service rather than in this module so the MCP server, the other entry point, gets the
same admission without repeating it. The middleware's redaction is the backstop. A request
that fails validation is answered without echoing its input.

## Cached examples

`extant/agent/cached_examples.json`, a file in the package, built in Phase 5 from recorded
answers and loaded into memory at start. They were never in Postgres, so nothing moved. Each
records the build it was answered on; if that differs from the build being served, the
examples are served anyway with `stale: true` and a message saying so. With no usable file the
endpoint serves the static explanation and no examples.

## Probes

| Probe | Path | Requires |
|---|---|---|
| Startup | `GET /health` | the manifest check (schema present, migrations, verification, embedding model, the public tables serving writes) and a successful query |
| Liveness | `GET /health/live` | only that the process answers |

A container whose build fails the manifest check, or that cannot reach Postgres at start,
never passes its startup probe, so it never receives traffic and the previous revision keeps
serving. Once started, losing the database must not fail liveness: the container keeps
answering from its cached examples, which is what the ladder is for, rather than being killed
and restarted into the same outage. Do not point a readiness-style probe at `/health` either:
during an outage it would take every instance out of service, and nobody would get the
examples. Phase 10 wires the probes; Cloud Run's behaviour here is not verified until then.

`GET /health` also reports the day's remaining cap. That discloses a little about usage (how
many answers were given today). For this project that is acceptable, arguably useful, and it
is stated here rather than left unremarked.

## Rate limiting

Per client address, in memory, a sliding minute: 6 `POST /ask` and 120 reads by default
(`EXTANT_API_ASK_PER_MINUTE`, `EXTANT_API_READS_PER_MINUTE`). Health is not limited.

The client address is the socket's peer unless `EXTANT_API_TRUSTED_PROXY_HOPS` says how many
proxies in front of the process append to `X-Forwarded-For`; then it is that many entries
from the right, since entries further left were supplied by the caller. The default is 0:
trust no header, which is right locally and behind any proxy not configured on purpose.
Behind a Google load balancer the header ends `client, load balancer`, so 2. On Cloud Run the
socket peer is Google's infrastructure, so with 0 every user would share one limit; what a
direct `run.app` URL appends is confirmed against the running service in Phase 10.

**The limit is approximate.** Each instance counts on its own, and a cold start forgets: with
several instances a caller can make several times the limit, and scaling to zero resets it.
That is acceptable because it is anti-abuse only. The daily cap is the cost control, and it
is exact: a row in `public.usage_daily`, outside every build schema, moved in the transaction
that records the request. This layer reads it and never counts on its own.

## Connections

One `psycopg_pool` pool per process, sized for one container (1 to 4 connections), because
instances are independent. A connection attempt times out after 4 s and waiting for a pooled
connection after 4 s, so a database waking from autosuspend, or gone, reaches the ladder (one
retry, then cached examples) instead of hanging a request.

In production the pool connects to Neon's pooler (PgBouncer, transaction mode), which absorbs
connection churn and the first slow connection after idle. Two things follow from that pooler,
both handled in the layer below:

- A session `SET search_path` does not survive it: each autocommit statement may run on a
  different server connection (48 of 60 statements lost the setting in a test against Neon),
  and the pooler refuses `search_path` as a startup option. So every unit of work runs in one
  transaction with `SET LOCAL search_path` (`extant.core.schema.in_build`).
- Prepared statements are off, since one prepared on a server connection is not on the next.

## Latency

Measured with the server under uvicorn on one machine, 200 sequential requests after one warm
up, 23 November 2026:

| Endpoint | Local Postgres p50 / p95 | Neon (us-east-2) from Europe p50 / p95 |
|---|---|---|
| `GET /examples` (16 KiB) | 0.7 / 0.8 ms | 0.7 / 0.8 ms |
| `GET /paragraph/{id}` (2 KiB) | 3.0 / 4.0 ms | 644 / 649 ms |
| `GET /health/live` | 0.6 / 1.0 ms | 0.6 / 1.1 ms |

`/examples` never touches the database, so it does not move. A paragraph costs five round
trips (the pool's connection check, `BEGIN`, `SET LOCAL`, the query, `COMMIT`); from Europe to
Neon in Ohio one round trip measured 121 ms, which is the whole of the 644. Pipelining them did
not help through the pooler (607 ms). In the database's region a round trip is about a
millisecond, so the API belongs next to it: Neon `aws-us-east-2` is Ohio, and the nearest Cloud
Run region is `us-east5` (Columbus). Phase 10 decides.

## A known boundary: statelessness

The serving path is stateless per request: no checkpointer, no session, nothing kept between
requests but the pool and the in-memory rate limiter. That is why MCP's move to stateless
transport cost nothing, and why any number of instances can serve.

One planned mode breaks it: reviewing a whole uploaded policy document against the corpus.
That is minutes long, fans out over dozens of provisions, and needs checkpointing, progress
reporting and resumption. It does not fit this request and response shape and is not
promised here; it is named so the stateless design reads as a decision, not an oversight.
