"""The three prompts. Business rules live in the deterministic nodes, not here: a prompt
asks for a shape, and the code checks what comes back."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from extant.agent.models import Candidate

PARSE_SYSTEM = """\
You read one question for a system that answers only from Reserve Bank of India (RBI)
regulatory directions: the rules RBI issues for banks, NBFCs, co-operative banks and other
entities it regulates. Fill in the fields from the question alone; never answer it.

in_scope: true if the question is about what an RBI regulation requires, permits or applies
to. false for anything else, including other regulators, tax, markets, or general chat.
computation_requested: true if it asks to calculate a figure for the asker's own
institution, or to recommend or choose an option for them. Asking what a rule says, even
about a number, is not computation.
query_kind: applicability (who or what a rule applies to, or what it requires), traceback
(what replaced or repealed an earlier instrument), diff (how a provision changed between two
dates or versions), or as_of (what the rule was on a stated date).
entity_type: one code from the list below if the question names that kind of entity, else
null. Do not guess.
as_of: the date the question asks about, if it states one, else null. compare_as_of: for a
diff question, the other date, else null. Dates as YYYY-MM-DD.
topic_terms: two to six short terms naming what the question is about.

Entity codes:
{entities}
"""

COMPOSE_SYSTEM = """\
You answer a question using only the numbered paragraphs given, which are RBI direction
paragraphs in force for the entity and on the date stated. Write a short answer as claims.

Each claim is one plain statement. candidate_indices lists the bracketed numbers of the
paragraphs that support it. quotes lists exact substrings copied character for character
from those paragraphs that show it is true; copy, never paraphrase or fix spelling, and keep
each quote to the words that matter.

Every claim is checked on its own, against its own quotes. Every number a claim states must
appear in one of that claim's quotes, including a number an earlier claim already gave: quote
it again rather than refer back to it.

The bracketed numbers are for candidate_indices only. The asker reads the claim text, so it
never contains a bracketed number or any identifier; if a claim names a paragraph at all, it
uses the number the paragraph itself prints.

If the paragraphs do not establish an answer, set insufficient to true, give no claims, and
say in missing what they fail to establish. That is a correct response, not a failure: an
answer the paragraphs do not support is worse than none. Do not give advice, and do not
compute figures for the asker.
"""

JUDGE_SYSTEM = """\
You are shown one claim and one or more paragraphs of RBI regulation. Decide only whether the
paragraphs support the claim. supported is true if the claim follows from the text of the
paragraphs without outside knowledge or assumptions, and false otherwise.
"""


def parse_system(entities: Mapping[str, str]) -> str:
    listed = "\n".join(f"- {code}: {label}" for code, label in sorted(entities.items()))
    return PARSE_SYSTEM.format(entities=listed or "- (none loaded)")


def compose_user(
    question: str, entity: str | None, as_of: date, candidates: Sequence[Candidate]
) -> str:
    who = entity or "any entity type (none was given)"
    blocks = []
    for i, c in enumerate(candidates):
        where = f"paragraph {c.paragraph_number}" if c.paragraph_number else "paragraph"
        heading = f" ({c.heading_path})" if c.heading_path else ""
        blocks.append(f"[{i}] {c.doc_title}, {where}{heading}\n{c.body}")
    return (
        f"Question: {question}\nEntity type: {who}\nIn force on: {as_of.isoformat()}\n\n"
        + "\n\n".join(blocks)
    )


def judge_user(claim: str, bodies: Sequence[str]) -> str:
    return f"Claim: {claim}\n\nParagraphs:\n" + "\n\n".join(bodies)
