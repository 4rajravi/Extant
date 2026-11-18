import math
from datetime import date

import pytest
from pydantic import ValidationError

from extant.core.authority import AuthorityRank
from extant.core.constants import EMBEDDING_DIM
from extant.core.hashing import sha256_hex
from extant.core.models import (
    BuildManifest,
    Citation,
    CitationError,
    ConcordanceEntry,
    EntityType,
    Facet,
    ParaIdAlias,
    Withdrawal,
)
from tests import factories as f


def test_factories_build_valid_rows() -> None:
    f.document()
    f.paragraph()
    f.chunk()


# --------------------------------------------------------------------------- hashes


@pytest.mark.parametrize(
    "bad",
    [
        "a" * 63,  # short
        "a" * 65,  # long
        "A" * 64,  # uppercase
        "g" * 64,  # not hex
        "",
    ],
)
def test_short_or_malformed_hash_is_rejected(bad: str) -> None:
    with pytest.raises(ValidationError, match="source_sha256"):
        f.document(source_sha256=bad)
    with pytest.raises(ValidationError, match="content_hash"):
        f.paragraph(content_hash=bad)
    with pytest.raises(ValidationError, match="content_hash"):
        f.chunk(content_hash=bad)


def test_paragraph_hash_must_be_of_its_body() -> None:
    with pytest.raises(ValidationError, match=r"sha256\(body\)"):
        f.paragraph(content_hash=sha256_hex("some other text"))


def test_chunk_hash_is_of_embed_text_not_body() -> None:
    chunk = f.chunk()
    assert chunk.content_hash == sha256_hex(chunk.embed_text)
    with pytest.raises(ValidationError, match=r"sha256\(embed_text\)"):
        f.chunk(content_hash=sha256_hex(f.BODY))


# --------------------------------------------------------------------------- chunk rules


def test_empty_entity_codes_are_rejected() -> None:
    with pytest.raises(ValidationError, match="entity_codes"):
        f.chunk(entity_codes=())


@pytest.mark.parametrize("codes", [("nbfc_ul", "nbfc_ul"), ("NBFC_UL",), ("nbfc-ul",)])
def test_bad_entity_codes_are_rejected(codes: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        f.chunk(entity_codes=codes)


@pytest.mark.parametrize("valid_to", [date(2025, 3, 31), date(2025, 4, 1)])
def test_valid_to_must_be_strictly_after_valid_from(valid_to: date) -> None:
    with pytest.raises(ValidationError, match="strictly after"):
        f.chunk(valid_from=date(2025, 4, 1), valid_to=valid_to)


def test_valid_at_is_half_open() -> None:
    chunk = f.chunk(valid_from=date(2025, 4, 1), valid_to=date(2026, 4, 1))
    assert not chunk.valid_at(date(2025, 3, 31))
    assert chunk.valid_at(date(2025, 4, 1))  # closed at the start
    assert chunk.valid_at(date(2026, 3, 31))
    assert not chunk.valid_at(date(2026, 4, 1))  # open at the end
    open_ended = f.chunk(valid_from=date(2025, 4, 1))
    assert open_ended.valid_at(date(2099, 1, 1))


@pytest.mark.parametrize("length", [0, EMBEDDING_DIM - 1, EMBEDDING_DIM + 1, 1536])
def test_embedding_must_have_256_dimensions(length: int) -> None:
    with pytest.raises(ValidationError, match="embedding"):
        f.chunk(embedding=(0.0,) * length)


@pytest.mark.parametrize("value", [math.nan, math.inf, 70000.0, -70000.0])
def test_embedding_values_must_fit_halfvec(value: float) -> None:
    with pytest.raises(ValidationError, match="embedding"):
        f.chunk(embedding=(value,) + (0.0,) * (EMBEDDING_DIM - 1))


def test_chunk_para_must_belong_to_its_doc() -> None:
    with pytest.raises(ValidationError, match="does not belong"):
        f.chunk(doc_id="rbi:md:2025:0148:v1")


# --------------------------------------------------------------------------- documents


@pytest.mark.parametrize("rank", [0, 35, 80, -10])
def test_authority_rank_must_be_an_allowed_rank(rank: int) -> None:
    with pytest.raises(ValidationError, match="authority_rank"):
        f.document(authority_rank=rank)
    with pytest.raises(ValidationError, match="authority_rank"):
        f.chunk(authority_rank=rank)


def test_authority_rank_accepts_its_integer_value() -> None:
    assert f.document(authority_rank=60).authority_rank is AuthorityRank.FAQ


@pytest.mark.parametrize("doc_id", ["rbi:md:2025:147:v1", "RBI:md:2025:0147:v1", "x"])
def test_document_rejects_malformed_doc_id(doc_id: str) -> None:
    with pytest.raises(ValidationError, match="doc_id"):
        f.document(doc_id=doc_id)


def test_document_identity_must_agree_with_its_fields() -> None:
    with pytest.raises(ValidationError, match="corpus"):
        f.document(corpus="gst")
    with pytest.raises(ValidationError, match="version_seq"):
        f.document(version_seq=2)
    with pytest.raises(ValidationError, match="supersedes"):
        f.document(doc_id="rbi:md:2025:0147:v2", version_seq=2)
    with pytest.raises(ValidationError, match="supersede itself"):
        f.document(supersedes_doc_id=f.DOC_ID)
    amended = f.document(doc_id="rbi:md:2025:0147:v2", version_seq=2, supersedes_doc_id=f.DOC_ID)
    assert amended.supersedes_doc_id == f.DOC_ID


def test_document_effective_dates_mirror_the_check_constraint() -> None:
    with pytest.raises(ValidationError, match="effective_to"):
        f.document(effective_from=date(2025, 4, 1), effective_to=date(2025, 3, 31))
    f.document(effective_from=date(2025, 4, 1), effective_to=date(2025, 4, 1))


@pytest.mark.parametrize("url", ["ftp://example.org/x.pdf", "example.org", "https://"])
def test_document_source_url_must_be_http(url: str) -> None:
    with pytest.raises(ValidationError, match="source_url"):
        f.document(source_url=url)


def test_models_are_frozen_and_closed() -> None:
    doc = f.document()
    with pytest.raises(ValidationError):
        doc.title = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="Extra inputs"):
        f.document(unexpected="field")


# --------------------------------------------------------------------------- paragraphs


def test_paragraph_must_belong_to_its_doc() -> None:
    with pytest.raises(ValidationError, match="does not belong"):
        f.paragraph(doc_id="rbi:md:2025:0148:v1")


@pytest.mark.parametrize("body", ["", "   ", "\n\t"])
def test_paragraph_body_must_not_be_blank(body: str) -> None:
    with pytest.raises(ValidationError, match="body"):
        f.paragraph(body=body, content_hash=sha256_hex(body))


def test_paragraph_body_is_kept_verbatim() -> None:
    body = "  4.3A  An NBFC shall report.\n"
    assert f.paragraph(body=body).body == body


# --------------------------------------------------------------------------- other tables


def test_facet_ref_must_match_its_scope() -> None:
    Facet(scope="document", ref_id=f.DOC_ID, key="regulator", value="RBI")
    Facet(scope="paragraph", ref_id=f.PARA_ID, key="topic", value="reporting")
    with pytest.raises(ValidationError):
        Facet(scope="document", ref_id=f.PARA_ID, key="regulator", value="RBI")
    with pytest.raises(ValidationError):
        Facet(scope="paragraph", ref_id=f.DOC_ID, key="topic", value="reporting")
    with pytest.raises(ValidationError):
        Facet.model_validate({"scope": "chunk", "ref_id": f.DOC_ID, "key": "k", "value": "v"})


def test_entity_type() -> None:
    EntityType(code="nbfc_ul", label="NBFC - Upper Layer")
    with pytest.raises(ValidationError):
        EntityType(code="NBFC UL", label="NBFC - Upper Layer")


def test_concordance_entry() -> None:
    entry = ConcordanceEntry(
        old_ref="DBOD.BP.BC.1/21.04.048/2015-16",
        new_doc_id=f.DOC_ID,
        new_para_id=f.PARA_ID,
        relation="consolidated_into",
        evidence_doc_id=f.DOC_ID,
        evidence_page=212,
    )
    assert entry.relation == "consolidated_into"
    with pytest.raises(ValidationError, match="relation"):
        ConcordanceEntry.model_validate(
            {"old_ref": "X", "relation": "amended", "evidence_doc_id": f.DOC_ID}
        )
    with pytest.raises(ValidationError, match="new_para_id"):
        ConcordanceEntry(
            old_ref="X",
            new_doc_id="rbi:md:2025:0148:v1",
            new_para_id=f.PARA_ID,
            relation="consolidated_into",
            evidence_doc_id=f.DOC_ID,
        )


def test_withdrawal_keeps_announcement_and_effect_apart() -> None:
    w = Withdrawal(
        circular_ref="DBOD.BP.BC.1/21.04.048/2015-16",
        withdrawn_by_doc_id=f.DOC_ID,
        announced_on=date(2025, 4, 1),
        effective_on=date(2025, 10, 1),
    )
    assert w.announced_on < w.effective_on
    with pytest.raises(ValidationError, match="withdrawn_by_doc_id"):
        Withdrawal(
            circular_ref="X",
            withdrawn_by_doc_id="not-an-id",
            announced_on=date(2025, 4, 1),
            effective_on=date(2025, 4, 1),
        )


def test_para_id_alias() -> None:
    ParaIdAlias(
        old_para_id="rbi:md:2025:0147:v1#ch4.3",
        new_para_id=f.PARA_ID,
        retired_in="idx_rbi_20261118_a3f91c4",
        reason="renumbered",
    )
    with pytest.raises(ValidationError, match="retired_in"):
        ParaIdAlias(old_para_id=f.PARA_ID, retired_in="public", reason="renumbered")
    with pytest.raises(ValidationError, match="alias itself"):
        ParaIdAlias(
            old_para_id=f.PARA_ID,
            new_para_id=f.PARA_ID,
            retired_in="idx_rbi_20261118_a3f91c4",
            reason="renumbered",
        )


def test_build_manifest_holds_json() -> None:
    assert BuildManifest(key="migrations", value=["0001_initial"]).value == ["0001_initial"]


# --------------------------------------------------------------------------- citations


def test_citation_verifies_against_the_body() -> None:
    para = f.paragraph()
    Citation(para_id=f.PARA_ID, quote="within 21 days", source_page=17).verify(para)


def test_citation_cannot_quote_the_composed_header() -> None:
    chunk = f.chunk()
    header_quote = "Applies to: NBFC - Upper Layer"
    assert header_quote in chunk.embed_text
    with pytest.raises(CitationError, match="verbatim"):
        Citation(para_id=f.PARA_ID, quote=header_quote, source_page=17).verify(f.paragraph())


def test_citation_rejects_a_paraphrase_and_the_wrong_paragraph() -> None:
    para = f.paragraph()
    with pytest.raises(CitationError, match="verbatim"):
        Citation(para_id=f.PARA_ID, quote="within twenty one days", source_page=17).verify(para)
    with pytest.raises(CitationError, match="checked against"):
        Citation(
            para_id="rbi:md:2025:0147:v1#ch4.3B", quote="within 21 days", source_page=17
        ).verify(para)


def test_citation_fields_are_validated() -> None:
    with pytest.raises(ValidationError, match="para_id"):
        Citation(para_id=f.DOC_ID, quote="x", source_page=1)
    with pytest.raises(ValidationError, match="quote"):
        Citation(para_id=f.PARA_ID, quote=" ", source_page=1)
    with pytest.raises(ValidationError, match="source_page"):
        Citation(para_id=f.PARA_ID, quote="x", source_page=0)
    assert Citation(para_id=f.PARA_ID, quote="x", source_page=1).doc_id == f.DOC_ID
