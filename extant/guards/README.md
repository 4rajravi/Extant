# Guardrails (Phase 5)

Guardrails here are product features, not a compliance afterthought: the refusal message is a
surface a user reads, and the adversarial suite is what makes the refusal taxonomy real.

## Where each guard sits, and why there

| Guard | Placement | Why there |
|---|---|---|
| Sensitive data redaction | Admission (`agent/service.py`), and middleware as a backstop | Before the state, the embedding, any model or log sees the question; the backstop binds a model call added later |
| Token and cost accounting | Middleware (`middleware.py`) | A node that forgets to log its cost would make the budget fiction |
| Daily cap | Middleware, and admission | The cap binds every call or it binds nothing |
| Retry and backoff | Middleware | One policy, one place to tune; the provider client's own retries are off |
| Scope, computation and advice | Node (`parse`) | Needs a model and decides the path |
| Supersession gate | Node (`gate`), deterministic | Changes the candidate set, so it is a graph step |
| Authority and binding | Node (`authority`) and post check | The node refuses; the post check proves no answer stands on non-binding material alone |
| Citation existence, quote match, supersession, numerics, plain text | Post checks (`post.py`) | Pure functions of the state |
| Groundedness, first pass | Post check | Content word overlap, no model |
| Groundedness, second pass | Node (`judge`) | Needs a model; runs on the residual claims only |

The post checks are called from two places with two failure modes. `verify` turns a failure
into `verification_failed`, a user facing refusal that names what failed. `finalize` runs the
same functions over the final state and raises: reaching `finalize` with a failing check means
a path bypassed `verify`, which is a bug.

The middleware wraps the model object. The only way to obtain a model is
`AgentSettings.models(store)`, which returns `GuardedModel`s; a test proves that a throwaway
call made outside the graph is redacted, counted and capped, and a boundary test proves no
other module builds a model client.

## Sensitive data

`pii.py` covers what a generic library misses: PAN, GSTIN, Aadhaar (12 digits, often 4-4-4),
bank account numbers (any run of 9 or more digits after account words), IFSC, CIN, Indian
mobile numbers, email addresses and any bare run of 13 or more digits. Each match becomes a
type token (`<PAN>`), so the redacted question still reads sensibly. When a pattern is
ambiguous it redacts: a false positive is cheap and a false negative is not. The response
says what kind of thing was removed. The raw question is never persisted; the request ledger
and the log hold the redacted form and its sha256.

Measured on 1,292 eval queries and all 62,920 corpus paragraphs: no match, once the
regulator's own public mailboxes (`…@rbi.org.in`, printed in 91 paragraphs) are exempt.

## The cap

50 answered questions a day, India time. The counter is in Postgres (`public.usage_daily`),
not in process: on Cloud Run with minimum instances zero an in process counter resets on every
cold start and is not shared between instances, so it would let the cap be exceeded several
times over while every test passed. Each request's ledger row and the counter it moves are
written in one transaction. Answered questions are counted, and model calls beside them. A
counter that cannot be read refuses the call: the cap fails closed. At the cap the service
serves cached example answers with the reason `cap_reached`.

## The degradation ladder

| Rung | Marker in `degraded` | Reached when |
|---|---|---|
| 1. Full path | none | Everything works |
| 2. No rerank | not a rung | Serving has no reranker (Phase 3 measured one worse), so its absence is normal |
| 3. Retrieval only | `retrieval_only`, `lexical_only` | A model fails after one retry (provisions shown whole, no prose); or the embedding call fails (lexical retrieval) |
| 4. Cached examples | `cached_examples` (`stale_examples` if built on another schema) | Postgres unreachable after one retry, or the cap reached |
| 5. Static page | `static_page` | Rung 4 with no usable example cache |

| Dependency | Failure | Detection | Behaviour |
|---|---|---|---|
| Generation model | Unavailable or rate limited | Exception after one retry with backoff (`ModelUnavailableError`) | Retrieved provisions, no composed answer, `retrieval_only` |
| Embedding API | Unavailable at query time | `EmbeddingError` on the query embed call | Lexical only retrieval, `lexical_only` and the `degraded_retrieval` caveat |
| Postgres | Unreachable or waking from autosuspend | Connection error, retried once | Cached examples only: the one dependency with no graceful degradation |
| Index schema | Missing, or failing its manifest check | `service.readiness()` at container start | Not ready: the previous revision keeps serving |
| Daily cap | Reached | The counter, at admission and before every model call | Cached examples with `cap_reached` |
| RBI website | Unreachable | Scraper error | No serving impact: the index plane works from its cache |
| Cold start | First request after idle | Expected | Accepted; ping before a demo |
| Groundedness or a citation check | Fails | By design | A refusal naming what could not be established |

The cached examples (`extant/agent/cached_examples.json`) are real answers from the recorded
fixtures, built with `uv run python -m extant.agent.examples build`, each stamped with the
build it came from.

## Request logging

One JSON line per request on stdout (`logs.py`): the redacted question and its hash, entity,
date, path, what was retrieved with scores, what the gate dropped, citations, outcome,
refusal reason, caveats, per node cost and latency, total cost, the degraded list and the
kinds of identifier redacted. Retrieved and dropped are logged with the schema name so a
disputed answer can be replayed against the exact build. `summarise()` gives the counters
worth watching: outcomes, refusal reasons, the fast path's share, paragraphs dropped per
request, answered questions against the cap by day, model calls, and cost at p50 and p95.
Alias hits on `para_id` resolution are not counted yet: nothing in serving resolves a retired
`para_id` until the citation lookup exists. A test runs the redactor's own pattern set over
every log record the fixtures produce.

## The adversarial suite

`evals/adversarial.py`, run in CI by `tests/integration/test_adversarial.py` on recorded
fixtures with the network blocked. Every case asserts its specific stated reason or caveat;
every refusal case has an answerable twin; all eight refusal reasons and all five caveats are
exercised. Results, with each refusal message for reading, are in
[`evals/adversarial-results.md`](../../evals/adversarial-results.md):

```bash
uv run python -m extant.agent.record --schema <build>   # records the questions, about €0.45
uv run python -m extant.agent.examples build            # refreshes the cached examples
uv run python -m evals.adversarial render               # writes the results file
```
