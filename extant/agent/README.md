# The agent graph (Phase 4)

The graph holds control flow. It does not hold business rules in prompts, write SQL, or rank:
its job is to make a wrong answer structurally hard to produce. Eleven nodes and one routing
edge, acyclic, stateless per request: no checkpointer, no memory, no tool loop, and no retry
into `compose` after a failed verification (a Phase 6 ablation decides whether one earns its
doubled worst case cost).

```
parse -(route)-> refuse_early ---------------------------------------------> finalize
             \-> resolve_reference ----------\
              \-> retrieve -> expand --------> gate -> authority -> compose -> verify -> judge -> finalize
```

After `gate`, `authority`, `compose` and `verify`, a refusal reason goes straight to `finalize`.

## Routing, in precedence order

| Signal | Path | Model calls |
|---|---|---|
| Out of scope, by pattern or by the cheap model | `out_of_scope`, refused | 0 by pattern, 1 by model |
| A computation or choice for the asker's institution | `refuse`, refused | 0 by pattern, 1 by model |
| A literal instrument reference | `fast` | 0 |
| A diff question with two dates | `diff` | 1 (parse) |
| Everything else | `full` | 2 or 3 |

The deterministic patterns run first on every question, which is what lets the fast path make
zero model calls while an out of scope question carrying a reference still exits.

## Nodes

Only `parse`, `compose` and `judge` call a model (Claude Haiku 5.5, Opus 5.5 and Haiku 5.5, built
through LangChain's `init_chat_model` from `.env`). Only `retrieve` and `resolve_reference` read
the database. `run_node` (`runtime.py`) hands each node only what it may use, and enforces the
invariant table on every hop:

| Field | Written by | Invariant |
|---|---|---|
| `entity_type`, `as_of` | `parse` | Set exactly once |
| `candidates` | `retrieve`, `expand`, `resolve_reference`, then `gate`, `authority` | Every removal has a `dropped` row; after the gate they only shrink |
| `dropped`, `set_aside`, `caveats`, `degraded`, `node_log` | as listed in `runtime.WRITES` | Append only |
| `draft_claims` | `compose` | Every citation is a candidate (validator) |
| `verified_claims` | `verify` | Non empty whenever the outcome is an answer (validator) |
| `node_log` | every node | One row per node executed, including zero cost rows |

## The composer contract

The composer sees the candidates as a numbered list and names them only by number
(`ComposerClaim.candidate_indices`). A resolve step turns numbers into para_ids
(`DraftClaim.citation_ids`); an index out of range fails there. Quotes are located with
`str.find` against the paragraph body, never `embed_text`; the code supplies the offsets.
`insufficient` is a first class outcome, `no_support_found`.

## Verification

Five deterministic checks per claim (`checks.py`): the spec's four, citation existence, quote
match, supersession and numerics (`numerics.py`, normalised; every failure logged with both
strings), and plain text, which withholds a claim whose prose carries an internal label (the
composer's `[3]`, or a para_id) that the asker would otherwise read. Then
groundedness pass 1, content word overlap; a claim below 0.5 goes to the judge, which sees only
the claim and its paragraphs. Any failure is `verification_failed`. Claims written by code on
the fast and diff paths pass the same four checks and skip only the judge.

## Refusals and caveats

Seven refusal classes are raised by the graph (`models.RefusalReason`); the eighth,
`cap_reached`, is served by the service around the graph (`service.py`) when the daily cap is
reached, with cached example answers. Phase 5 added the service, the middleware and the
degradation ladder; see [`extant/guards/README.md`](../guards/README.md). Messages (`refusals.py`) name the entity and date,
say what was found, suggest rephrasing only for `out_of_scope` and `entity_required`, and end
with "Check the instrument itself before relying on this." where something was found but not
stood behind. Five caveats attach to answers and never replace them.

## Cost

Every node appends a `NodeCost`; prices are in `pricing.py`. `AskResponse.cost_eur` and
`latency_ms` are sums over the log.

## Fixtures

```bash
uv run python -m extant.agent.record --schema idx_rbi_20261120_38ede3c   # needs EXTANT_ANTHROPIC_API_KEY
uv run pytest tests/integration/test_agent_replay.py                    # replays, no network
uv run python -m evals.judge_labels draw                                # judge pairs for hand labels
```

`record` runs each question through the real service (admission, middleware, graph) on the
real build and keeps every index and model call in `tests/fixtures/agent/`: the Phase 4
questions and the Phase 5 adversarial cases and their twins (`adv_`). Five more fixtures are
derived from a recorded answer (`full_answer_third_person`) by a single named change, for
outcomes the corpus cannot produce (it holds no FAQ or press release, and retrieval degrades
only when the embedding call fails). A recorded fixture keeps what happened when it was
recorded, refusals included.
