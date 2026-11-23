"""A small synthetic corpus for the index and retrieval integration tests.

Five document versions with overlapping entity scopes and validity windows chosen to hit the
edges: a version boundary on 1 January 2026 (D1 v1 ends, v2 begins), a window that closes on
15 September 2025 (D3), a multi-category direction (D4), and an NBFC direction with an
oversized paragraph that the chunker splits (D2). Titles use words that appear in no body, so a
lexical match on them would prove ``fts`` read the header. One paragraph mentions a withdrawn
circular, which the withdrawals and concordance rows describe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from extant.core.identity import canonical_ref, withdrawal_key
from extant.core.models import ConcordanceEntry, EntityType, Facet, Withdrawal
from extant.corpora.rbi.chunk import chunk_paragraphs
from extant.index.assemble import BuildInputs
from tests import factories as f

ENTITIES = (
    EntityType(code="cb", label="Commercial Banks"),
    EntityType(code="sfb", label="Small Finance Banks"),
    EntityType(code="nbfc", label="Non-Banking Financial Companies"),
    EntityType(code="nbfc_hfc", label="Housing Finance Companies"),
    EntityType(code="ucb", label="Urban Co-operative Banks"),
)
LABEL = {e.code: e.label for e in ENTITIES}
TOPICS = (
    "dividend",
    "capital adequacy",
    "large exposure",
    "fraud reporting",
    "interest rate",
    "outsourcing",
    "liquidity",
    "board approval",
    "customer grievance",
    "provisioning",
)
REF_SURFACE = "DBOD.No.BP.BC.12/21.04.048/2014-15"
REF = canonical_ref(REF_SURFACE)
OVERSIZED = " ".join(
    f"Sentence {j} sets out how the company shall measure its liquidity coverage each day."
    for j in range(1, 160)
)


@dataclass(frozen=True)
class Spec:
    doc_id: str
    title: str
    codes: tuple[str, ...]
    valid_from: date
    valid_to: date | None
    supersedes: str | None = None
    deleted: tuple[int, ...] = ()  # paragraph indexes replaced by a deletion marker


D1V1 = Spec(
    "rbi:md:2025:0001:v1", "Zebra Directions, 2025", ("cb",), date(2025, 1, 1), date(2026, 1, 1)
)
D1V2 = Spec(
    "rbi:md:2025:0001:v2", "Zebra Directions, 2025", ("cb",), date(2026, 1, 1), None, D1V1.doc_id
)
D2 = Spec(
    "rbi:md:2025:0002:v1", "Quokka Directions, 2025", ("nbfc", "nbfc_hfc"), date(2025, 6, 1), None
)
D3 = Spec(
    "rbi:md:2025:0003:v1", "Okapi Directions, 2025", ("sfb",), date(2025, 3, 15), date(2025, 9, 15)
)
D4 = Spec("rbi:md:2025:0004:v1", "Narwhal Directions, 2025", ("ucb", "cb"), date(2024, 1, 1), None)
SPECS = (D1V1, D1V2, D2, D3, D4)


def body(spec: Spec, i: int, topic: str) -> str:
    text = (
        f"{i + 1}. The {topic} requirement {i + 1} obliges the regulated entity to keep records"
        f" of its {topic} decisions and to report them every quarter."
    )
    if i in spec.deleted:
        return f"{i + 1}. *****"
    if spec is D2 and i == 6:
        return f"{i + 1}. {OVERSIZED}"
    if spec is D4 and i == 0:
        return f"{text} This direction repeals circular {REF_SURFACE} on dividends."
    if spec is D1V2:
        return text.replace("every quarter", "every month")
    return text


def build_inputs(
    *, drop_last: int = 0, unchunked: int = 0, extra: tuple[Spec, ...] = ()
) -> BuildInputs:
    """The synthetic corpus. ``drop_last`` removes that many paragraphs from each document;
    ``unchunked`` leaves that many paragraphs without chunk rows (to fail coverage); ``extra``
    adds documents beyond the five, for tests that need a shape the five do not have."""
    documents, paragraphs, facets, chunks = [], [], [], []
    for spec in (*SPECS, *extra):
        doc = f.document(
            doc_id=spec.doc_id,
            title=spec.title,
            issued_on=spec.valid_from,
            effective_from=spec.valid_from,
            supersedes_doc_id=spec.supersedes,
            version_seq=int(spec.doc_id.rsplit(":v", 1)[1]),
            source_url="https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=1",
        )
        documents.append(doc)
        paras = [
            f.paragraph(
                para_id=f"{spec.doc_id}#p{i + 1}",
                doc_id=spec.doc_id,
                ordinal=i,
                chapter="I",
                para_number=str(i + 1),
                heading_path=f"Chapter I > {topic.title()}",
                body=body(spec, i, topic),
                source_page=None,
                source_anchor=f"block:{i + 3}",
            )
            for i, topic in enumerate(TOPICS[: len(TOPICS) - drop_last])
        ]
        paragraphs.extend(paras)
        facets.extend(
            Facet(scope="document", ref_id=spec.doc_id, key="applies_to", value=c)
            for c in spec.codes
        )
        rows = chunk_paragraphs(
            doc,
            paras,
            entity_codes=spec.codes,
            entity_labels=[LABEL[c] for c in spec.codes],
            valid_from=spec.valid_from,
            valid_to=spec.valid_to,
        )
        chunks.extend(rows)
    if unchunked:
        dropped = {p.para_id for p in paragraphs[:unchunked]}
        chunks = [c for c in chunks if c.para_id not in dropped]
    withdrawal = Withdrawal(
        withdrawal_key=withdrawal_key(REF, date(2014, 7, 1), "Dividend circular"),
        circular_ref=REF_SURFACE,
        canonical_ref=REF,
        title="Dividend circular",
        circular_date=date(2014, 7, 1),
        withdrawn_by_doc_id="rbi:circ:2025:0100:v1",
        announced_on=date(2025, 11, 28),
        effective_on=date(2025, 11, 28),
    )
    concordance = ConcordanceEntry(
        old_ref=REF,
        old_title="Dividend circular",
        new_doc_id=D4.doc_id,
        relation="consolidated_into",
        evidence_doc_id=D4.doc_id,
        evidence_anchor="block:3",
    )
    return BuildInputs(
        documents=documents,
        paragraphs=paragraphs,
        facets=facets,
        entity_types=list(ENTITIES),
        chunks=chunks,
        concordance=[concordance],
        withdrawals=[withdrawal],
        manifest={"chunker_version": "test", "parser_version": "test"},
    )
