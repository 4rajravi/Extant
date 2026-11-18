"""Valid rows, built through the real models so test data obeys the same rules as
production data. Pass keyword overrides to vary one field."""

from __future__ import annotations

from datetime import date
from typing import Any

from extant.core.authority import AuthorityRank
from extant.core.constants import EMBEDDING_DIM
from extant.core.embedding import compose_embed_text
from extant.core.hashing import sha256_hex
from extant.core.models import Chunk, Document, Paragraph

DOC_ID = "rbi:md:2025:0147:v1"
PARA_ID = "rbi:md:2025:0147:v1#ch4.3A"
TITLE = "Master Direction - Non-Banking Financial Company - Scale Based Regulation, 2025"
HEADING = "Chapter IV > Reporting > 4.3A"
BODY = (
    "An NBFC in the Upper Layer shall report its large exposures to the Reserve Bank"
    " within 21 days from the end of each quarter."
)
LABELS = ("NBFC - Upper Layer",)


def document(**overrides: Any) -> Document:
    fields: dict[str, Any] = {
        "doc_id": DOC_ID,
        "corpus": "rbi",
        "title": TITLE,
        "instrument_type": "master_direction",
        "authority_rank": AuthorityRank.MASTER_DIRECTION,
        "issued_on": date(2025, 4, 1),
        "effective_from": date(2025, 4, 1),
        "source_url": "https://example.org/rbi/md/2025/0147.pdf",
        "source_sha256": sha256_hex("source pdf bytes"),
        "parse_status": "parsed",
    }
    fields.update(overrides)
    return Document.model_validate(fields)


def paragraph(**overrides: Any) -> Paragraph:
    fields: dict[str, Any] = {
        "para_id": PARA_ID,
        "doc_id": DOC_ID,
        "ordinal": 42,
        "chapter": "IV",
        "para_number": "4.3A",
        "heading_path": HEADING,
        "body": BODY,
        "source_page": 17,
    }
    fields.update(overrides)
    fields.setdefault("content_hash", sha256_hex(fields["body"]))
    return Paragraph.model_validate(fields)


def embedding(hot: int = 0) -> tuple[float, ...]:
    """A unit vector with a 1.0 at index ``hot``."""
    return tuple(1.0 if i == hot else 0.0 for i in range(EMBEDDING_DIM))


def chunk(**overrides: Any) -> Chunk:
    para = paragraph(
        **{k: overrides.pop(k) for k in ("para_id", "body", "doc_id") if k in overrides}
    )
    labels = overrides.pop("entity_labels", LABELS)
    embed_text = overrides.pop(
        "embed_text", compose_embed_text(document(doc_id=para.doc_id), para, labels)
    )
    fields: dict[str, Any] = {
        "para_id": para.para_id,
        "doc_id": para.doc_id,
        "body": para.body,
        "embed_text": embed_text,
        "entity_codes": ("nbfc_ul",),
        "valid_from": date(2025, 4, 1),
        "valid_to": None,
        "authority_rank": AuthorityRank.MASTER_DIRECTION,
        "embedding": embedding(),
    }
    fields.update(overrides)
    fields.setdefault("content_hash", sha256_hex(fields["embed_text"]))
    return Chunk.model_validate(fields)
