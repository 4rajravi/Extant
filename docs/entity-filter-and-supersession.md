# Two findings from Extant, the entity filter and supersession

Extant answers questions about the Reserve Bank of India's Master Directions for a given type of
regulated entity on a given date, with verified citations or a stated refusal. Two design choices
in it were tested by ablation. One came out weaker than the design argued and one came out
stronger. Every number below is lifted from a committed file, named beside it.

Not legal or tax advice.

## 1. The entity filter runs before the limit

A question about a small finance bank on a given date must not be answered from a provision that
applies only to payments banks, or from a version replaced before that date. Extant enforces
both with a hard filter. One SQL statement first materialises the set of chunks whose entity
scope overlaps the question's and whose validity covers the date, and only then do the dense and
lexical arms each rank that set and take their limit. There is no vector index. Distance is exact
over the filtered set.

The alternative is a post filter, where the search takes its top results over everything and the
filter runs afterwards. Over an approximate nearest neighbour index this is the usual shape, and
it fails silently. When the filter keeps a small share of rows, the index surfaces candidates that
nearly all fail it, the filter removes them, and the caller gets fewer results or none, with no
error raised.

The Phase 3 ablation built that alternative as an arm. Each arm takes its 50 best over the whole
build and the entity and date filter runs after. Because Extant has no approximate index, this arm
still ranks exactly, so it isolates the order of filtering and limiting and nothing else. These
rows are from the temporal set, where each query asks for one paragraph as it stood on a date,
from [`evals/ablations.md`](../evals/ablations.md). Label precision on that set is 0.96 on 50
pairs, audited by Claude against the cached RBI pages and not by a person.

Dev split

<!-- lift:prefilter-dev -->
| Arm | Corpus | n | Recall@1 | Recall@10 | MRR | Document recall@10 | Entity violations | As of violations | Differs from its reference arm at |
|---|---|---|---|---|---|---|---|---|---|
| `hybrid` | full | 160 | 0.919 [0.875, 0.956] | 0.988 [0.969, 1.000] | 0.944 [0.915, 0.971] | 1.000 [1.000, 1.000] | 0 of 7172 | 0 of 7172 | reference |
| `post-filter` | full | 160 | 0.875 [0.819, 0.925] | 0.956 [0.925, 0.988] | 0.910 [0.869, 0.947] | 0.975 [0.950, 0.994] | 0 of 2858 | 0 of 2858 | doc@5, doc@10, doc@20 |
<!-- /lift:prefilter-dev -->

Test split

<!-- lift:prefilter-test -->
| Arm | Corpus | n | Recall@1 | Recall@10 | MRR | Document recall@10 | Entity violations | As of violations | Differs from its reference arm at |
|---|---|---|---|---|---|---|---|---|---|
| `hybrid` | full | 128 | 0.898 [0.844, 0.945] | 0.992 [0.977, 1.000] | 0.932 [0.894, 0.965] | 0.992 [0.977, 1.000] | 0 of 5930 | 0 of 5930 | reference |
| `post-filter` | full | 128 | 0.859 [0.797, 0.914] | 0.961 [0.922, 0.992] | 0.901 [0.855, 0.942] | 0.969 [0.930, 0.992] | 0 of 1932 | 0 of 1932 | none |
<!-- /lift:prefilter-test -->

The design predicted two things, and only one of them showed up.

The silent shrinkage is real. The violation columns count every paragraph each arm returned. On
dev the post filter arm returned 2,858 paragraphs for 160 queries where the prefiltered arm
returned 7,172, and on test 1,932 for 128 queries against 5,930. Neither arm leaked a paragraph
outside the entity scope or the date, and neither raised an error. The post filter arm simply
handed back much less, and nothing told the caller.

The recall collapse did not happen on this set. Paragraph recall at 10 fell from 0.988 to 0.956
on dev and from 0.992 to 0.961 on test, and the intervals overlap, which the project reports as
no measurable difference. Only document recall on dev differed measurably. The likely reason is
the set, though no arm here tests it.
Each temporal query is the old paragraph's own first sentence, so the gold paragraph ranks near
the top of the whole build before any filter runs, and the filter has little to remove above it.
This set does not isolate a selective filter, such as an entity type that few provisions cover,
and that is the case where post filtering is expected to collapse. So the prefilter is justified
here by the shrinkage and by the design argument, and the collapse remains a prediction this
corpus has not yet tested.

## 2. Removing supersession improves a retrieval metric and makes the answers wrong

Directions are amended, and an amendment leaves the old version in the archive. Extant keeps
every version in the index with its validity window, and a gate after retrieval removes any
candidate not in force on the date asked, writing a row for each removal.

Phase 6 ran an arm with no supersession handling anywhere, so every version of every paragraph
competes in retrieval and nothing removes the old ones. The table is the side by side section of
[`evals/answers.md`](../evals/answers.md), run `2026-11-23`, both splits together. The as_of set
is 10 questions on 5 amended provisions, each asked on both sides of its change. The unanswerable
set is 37 questions. 10 of them are asked as of 30 June 2025, before the direction governing the
topic came into force, and 8 ask about a provision deleted, or a circular withdrawn, before the
date asked. The answerable set is 56.

<!-- lift:supersession -->
| Arm | Recall@10 of the version in force, as_of | Recall@10 of any version, as_of | Cited a version not in force, as_of | Gold cited, as_of | Correct refusals, unanswerable | Recall@10, answerable | Cost p50 (EUR) |
|---|---|---|---|---|---|---|---|
| baseline | 0.90 [0.70, 1.00] | 0.90 [0.70, 1.00] | 0 | 7/10 | 37/37 | 0.98 [0.95, 1.00] | 0.0248 |
| no_supersession | 0.80 [0.50, 1.00] | 1.00 [1.00, 1.00] | 6 | 5/10 | 27/37 | 0.91 [0.82, 0.98] | 0.0155 |
<!-- /lift:supersession -->

Read the two recall columns first. Without the gate, recall at 10 of any version of the gold
paragraph rises to 1.00, because the old version now always comes back. By that metric the arm
is better. Recall of the version actually in force falls from 0.90 to 0.80.

Then read the answers. Without the gate 6 answers cited a version that was not in force on the
date asked, against 0 with it. The gold version was cited 5 times in 10 instead of 7. 10
unanswerable questions got answers, 27 of 37 refused against 37 of 37. Of those 10, 8 were asked
as of 30 June 2025, when nothing governing them was yet in force, and 2 asked about a provision
deleted before the date asked. That breakdown is counted from
[`evals/answers/runs/2026-11-23/rows.jsonl`](../evals/answers/runs/2026-11-23/rows.jsonl) against
the reasons in
[`evals/answers/sets/unanswerable.jsonl`](../evals/answers/sets/unanswerable.jsonl). The arm
reported 0 quotes absent from the text they cited, since an old or future version's text is
real text, so the quote check cannot catch this. Only the date can. The arm was also the
cheapest, at a median of EUR 0.0155 a question against EUR 0.0248.

On 10 as_of questions the recall intervals overlap, so the recall difference is not measurable
by the project's own rule. The counts are the finding. A retrieval eval that scores any version
as a hit would have recommended removing the gate, and the answers show what that costs.

The repository and its full tables are described in the [README](../README.md).
