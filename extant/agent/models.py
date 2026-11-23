"""The agent's state and its response models.

The state is what flows between the graph's nodes; the response models are what ``finalize``
produces and the API will serve in Phase 8. Both are built so that the bad states this
design exists to prevent cannot be constructed:

- A draft claim citing a paragraph outside the candidate set raises
  ``CitationNotInCandidates`` (``AgentState.citations_must_be_candidates``).
- An answer with no verified claim behind it raises ``InvalidTerminalState``.
- An expanded neighbour carrying a fusion score, or a refusal with no reason, does not
  validate.

The rules that compare one state with the next (set once, append only, may only shrink after
the gate) cannot be a validator on a single state; ``extant.agent.runtime`` enforces them on
every hop.

Two objects deliberately share no name. ``ComposerClaim`` is what the model emits and holds
integer indices into the candidate list; ``DraftClaim`` is what state holds and carries
resolved ``para_id`` values. The resolve step between them is where an out of range index
fails.
"""

from __future__ import annotations

import operator
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

# --------------------------------------------------------------------------- vocabularies

QueryKind = Literal["applicability", "traceback", "diff", "as_of"]
# The design's five routes. The spec's state lists three; "diff" and "refuse" are the other
# two rows of its own routing table.
Path = Literal["out_of_scope", "refuse", "fast", "diff", "full"]


class RefusalReason(StrEnum):
    OUT_OF_SCOPE = "out_of_scope"
    COMPUTATION_REQUESTED = "computation_requested"
    ENTITY_REQUIRED = "entity_required"
    NO_SUPPORT_FOUND = "no_support_found"
    SUPERSEDED_ONLY = "superseded_only"
    NON_BINDING_SUPPORT_ONLY = "non_binding_support_only"
    VERIFICATION_FAILED = "verification_failed"
    # Raised by the API layer when the daily model call cap is spent (Phase 8). Defined here
    # so the response shape is settled; nothing in the graph raises it.
    CAP_REACHED = "cap_reached"


class Caveat(StrEnum):
    NOT_LEGAL_ADVICE = "not_legal_advice"
    NON_BINDING_CITED = "non_binding_cited"
    AS_OF_ASSUMED = "as_of_assumed"
    AMENDMENT_PENDING = "amendment_pending"
    DEGRADED_RETRIEVAL = "degraded_retrieval"


DropReason = Literal["superseded", "out_of_entity_scope", "not_yet_effective", "non_binding_only"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InvalidStateError(ValueError):
    """A state no node may produce."""


class CitationNotInCandidates(InvalidStateError):  # noqa: N818 - the spec's name
    def __init__(self, claim: DraftClaim, unknown: set[str]) -> None:
        super().__init__(f"claim cites {sorted(unknown)}, which are not candidates: {claim.text!r}")


class InvalidTerminalState(InvalidStateError):  # noqa: N818 - the spec's name
    pass


# --------------------------------------------------------------------------- internal objects


class Candidate(_Frozen):
    """A paragraph the retrieval layer returned, with everything a citation needs. Internal:
    ``Retrieved`` is its public projection."""

    para_id: str
    doc_id: str
    doc_title: str
    paragraph_number: str | None
    heading_path: str | None
    body: str  # the paragraph as published; quotes are located here and nowhere else
    ordinal: int
    rank: int | None  # 1-based among ranked results; None when expanded or looked up
    fusion_score: float | None
    lexical_rank: int | None
    dense_rank: int | None
    expanded: bool = False
    expanded_from: str | None = None
    origin: Literal["ranked", "expanded", "reference", "probe", "diff"]
    entity_scope: tuple[str, ...]
    valid_from: date
    valid_to: date | None
    authority_rank: int
    binding: bool
    source_url: str
    source_page: int | None = None
    # The date this candidate has to be in force on: the question's as-of date, or for the
    # diff path the other of its two dates.
    asked_for: date

    @model_validator(mode="after")
    def _expanded_rows_are_unscored(self) -> Candidate:
        if self.expanded and (self.fusion_score is not None or self.rank is not None):
            raise ValueError(f"{self.para_id}: an expanded neighbour carries no score or rank")
        if self.expanded != (self.origin == "expanded"):
            raise ValueError(f"{self.para_id}: expanded and origin disagree")
        return self

    def in_force(self, day: date | None = None) -> bool:
        on = day or self.asked_for
        return self.valid_from <= on and (self.valid_to is None or self.valid_to > on)


class WithdrawalRecord(_Frozen):
    withdrawn_by_doc_id: str
    effective_on: date
    title: str | None
    circular_date: date | None


class ReferenceRecord(_Frozen):
    """What the fast path's lookup found for one literal reference."""

    surface: str
    canonical_ref: str
    withdrawals: tuple[WithdrawalRecord, ...] = ()
    consolidated_into: tuple[str, ...] = ()


class Dropped(_Frozen):
    para_id: str
    reason: DropReason
    detail: str


class NodeCost(_Frozen):
    """One row per node executed, including the ones that spent nothing: a zero row is how
    the fast path's cost is proved rather than asserted."""

    node: str
    calls: int = 0  # model calls
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    embedding_calls: int = 0
    embedding_tokens: int = 0
    eur: float = 0.0
    latency_ms: float = 0.0


class CheckResult(_Frozen):
    name: str
    passed: bool
    detail: str = ""


class DraftClaim(_Frozen):
    """A composed claim with its citations resolved to para_ids. Quotes are still unlocated:
    ``verify`` finds each one in a cited body or kills the claim."""

    text: str
    citation_ids: tuple[str, ...] = Field(min_length=1)
    quotes: tuple[str, ...] = Field(min_length=1)
    # Written by code from a deterministic lookup (the fast and diff paths), not by a model.
    # It passes every hard check like any claim; only the judge, a second opinion on what a
    # model wrote, is skipped for it.
    templated: bool = False


# --------------------------------------------------------------------------- the model's own output


class ParseOutput(BaseModel):
    """What the cheap tier extracts from the question."""

    in_scope: bool
    computation_requested: bool
    query_kind: QueryKind
    entity_type: str | None = None
    as_of: date | None = None
    compare_as_of: date | None = None
    topic_terms: list[str] = []


class ComposerClaim(BaseModel):
    text: str
    candidate_indices: list[int] = Field(min_length=1)  # into the candidate list shown
    quotes: list[str] = Field(min_length=1)  # verbatim substrings of those candidates


class ComposerOutput(BaseModel):
    claims: list[ComposerClaim] = []
    insufficient: bool = False
    missing: str | None = None  # what the candidates failed to establish


class JudgeOutput(BaseModel):
    supported: bool


# --------------------------------------------------------------------------- responses


class Citation(_Frozen):
    para_id: str
    doc_id: str
    doc_title: str
    paragraph_number: str  # the regulator's own numbering
    effective_from: date
    effective_to: date | None
    entity_scope: list[str]
    authority_rank: int
    binding: bool
    source_url: HttpUrl
    # The spec has an int; the corpus is HTML, which has no pages, so a page exists only for
    # a paragraph read from a PDF.
    source_page: int | None
    quote_start: int  # character offsets into the stored paragraph body
    quote_end: int


class Claim(_Frozen):
    text: str
    citations: list[Citation] = Field(min_length=1)


class Retrieved(_Frozen):
    para_id: str
    fusion_score: float | None  # None for a looked up or expanded paragraph
    lexical_rank: int | None
    dense_rank: int | None
    expanded: bool


class Answer(_Frozen):
    outcome: Literal["answer"] = "answer"
    claims: list[Claim] = Field(min_length=1)
    caveats: list[Caveat]


class Refusal(_Frozen):
    outcome: Literal["refusal"] = "refusal"
    reason: RefusalReason
    message: str
    nearest: list[Citation]  # what it found but could not stand behind


class AskResponse(_Frozen):
    result: Answer | Refusal = Field(discriminator="outcome")
    as_of: date
    # The spec has an entity type always; a question asked with none that every candidate
    # agreed on has none to report.
    entity_type: str | None
    index_version: str
    retrieved: list[Retrieved]
    dropped: list[Dropped]
    degraded: list[str]
    cost_eur: float
    latency_ms: int


# --------------------------------------------------------------------------- the state


class AgentState(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # admission, written once, never mutated
    request_id: str
    index_version: str
    raw_query: str  # already redacted
    submitted_entity: str | None = None
    submitted_as_of: date | None = None
    today: date  # the date "today" means for this request, fixed at admission

    # parse
    query_kind: QueryKind | None = None
    entity_type: str | None = None
    as_of: date | None = None
    as_of_source: Literal["submitted", "question", "today"] | None = None
    compare_as_of: date | None = None  # the second date of a diff question
    literal_refs: list[str] = []  # canonical form
    surface_refs: list[str] = []  # as written in the question
    topic_terms: list[str] = []
    path: Path | None = None

    # retrieval
    candidates: list[Candidate] = []
    neighbours: list[Candidate] = []  # retrieve fetches them, expand admits them
    references: list[ReferenceRecord] = []
    gate_ran: bool = False
    dropped: Annotated[list[Dropped], operator.add] = []
    # The paragraphs behind the dropped rows, kept so a refusal can cite what it set aside
    # (superseded material with its dates, non-binding material marked as such).
    set_aside: Annotated[list[Candidate], operator.add] = []

    # compose
    draft_claims: list[DraftClaim] = []  # citation_ids only, no free text ids
    missing: str | None = None

    # verify and judge
    verified_claims: list[Claim] = []
    checks: dict[str, CheckResult] = {}
    residual: list[int] = []  # claims that passed the substring check, below the overlap

    # terminal
    outcome: Literal["answer", "refusal"] | None = None
    refusal_reason: RefusalReason | None = None
    refusal_detail: str | None = None
    caveats: Annotated[list[Caveat], operator.add] = []
    degraded: Annotated[list[str], operator.add] = []
    response: AskResponse | None = None

    # accounting, append only
    node_log: Annotated[list[NodeCost], operator.add] = []

    @model_validator(mode="after")
    def citations_must_be_candidates(self) -> AgentState:
        allowed = {c.para_id for c in self.candidates}
        for claim in self.draft_claims:
            unknown = set(claim.citation_ids) - allowed
            if unknown:
                raise CitationNotInCandidates(claim, unknown)
        return self

    @model_validator(mode="after")
    def answer_requires_verified_claims(self) -> AgentState:
        if self.outcome == "answer" and not self.verified_claims:
            raise InvalidTerminalState("answer with no verified claims")
        if self.outcome == "answer" and self.refusal_reason is not None:
            raise InvalidTerminalState("an answer carries no refusal reason")
        if self.outcome == "refusal" and self.refusal_reason is None:
            raise InvalidTerminalState("a refusal needs a reason")
        return self

    @model_validator(mode="after")
    def the_graph_never_refuses_for_the_cap(self) -> AgentState:
        if self.refusal_reason is RefusalReason.CAP_REACHED:
            raise InvalidStateError("cap_reached is raised by the API layer, never the graph")
        return self

    @model_validator(mode="after")
    def no_repealed_candidate_reaches_compose(self) -> AgentState:
        if self.draft_claims or self.verified_claims:
            stale = [c.para_id for c in self.candidates if not c.in_force()]
            if stale:
                raise InvalidStateError(f"claims composed over paragraphs not in force: {stale}")
        return self
