import json
from pathlib import Path
from typing import Any

import pytest

from extant.core.embedding import compose_embed_text
from extant.core.hashing import sha256_hex
from extant.core.models import Document, Paragraph
from tests import factories as f

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "embed_text.json"


def _load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["paragraph"]["content_hash"] = sha256_hex(data["paragraph"]["body"])
    return data


def test_embed_text_matches_fixture_exactly() -> None:
    data = _load()
    composed = compose_embed_text(
        Document.model_validate(data["document"]),
        Paragraph.model_validate(data["paragraph"]),
        data["entity_labels"],
    )
    assert composed == data["expected"]


def test_embed_text_ends_with_the_verbatim_body() -> None:
    para = f.paragraph()
    assert compose_embed_text(f.document(), para, f.LABELS).endswith("\n" + para.body)


def test_missing_heading_path_omits_the_line() -> None:
    para = f.paragraph(heading_path=None)
    assert compose_embed_text(f.document(), para, ["NBFC - Upper Layer"]) == (
        f"{f.TITLE}\nApplies to: NBFC - Upper Layer\n{f.BODY}"
    )


def test_embed_text_rejects_a_paragraph_from_another_document() -> None:
    other = f.paragraph(
        para_id="rbi:md:2025:0148:v1#ch1.1", doc_id="rbi:md:2025:0148:v1", body="Other."
    )
    with pytest.raises(ValueError, match="not a paragraph of"):
        compose_embed_text(f.document(), other, f.LABELS)


@pytest.mark.parametrize("labels", [[], [""], ["a\nb"], ["NBFC", "NBFC"]])
def test_embed_text_rejects_bad_entity_labels(labels: list[str]) -> None:
    with pytest.raises(ValueError, match="entity"):
        compose_embed_text(f.document(), f.paragraph(), labels)
