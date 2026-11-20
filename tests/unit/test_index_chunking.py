"""The splitter and chunk rows (step 5), offline."""

from __future__ import annotations

import itertools
from datetime import date

import pytest

from extant.core.hashing import sha256_hex
from extant.index.chunking import (
    MAX_CHUNK_TOKENS,
    ChunkDraft,
    estimate_tokens,
    make_draft,
    split_body,
)
from tests import factories as f


def sentence(i: int) -> str:
    return (
        f"Sentence {i} requires the regulated entity to report the exposure within"
        f" {i} days of the end of the quarter to the Reserve Bank."
    )


LONG = " ".join(sentence(i) for i in range(1, 120))


def test_a_paragraph_that_fits_is_one_piece() -> None:
    assert split_body(f.BODY) == [f.BODY]


def test_a_long_paragraph_splits_into_pieces_under_the_limit() -> None:
    assert estimate_tokens(LONG) > MAX_CHUNK_TOKENS
    pieces = split_body(LONG)
    assert len(pieces) > 1
    assert all(estimate_tokens(p) <= MAX_CHUNK_TOKENS for p in pieces)


def test_every_piece_is_an_exact_substring_of_the_body() -> None:
    for piece in split_body(LONG):
        assert piece in LONG


def test_pieces_overlap_by_exactly_one_sentence_and_cover_everything() -> None:
    pieces = split_body(LONG)
    for left, right in itertools.pairwise(pieces):
        last = left[left.rfind("Sentence ") :]
        assert right.startswith(last)
        assert left.count("Sentence ") > 1
    for i in range(1, 120):
        assert any(sentence(i) in p for p in pieces), f"sentence {i} is in no piece"
    assert pieces[0].startswith("Sentence 1 ")
    assert pieces[-1].endswith(sentence(119))


def test_abbreviations_and_enumerators_do_not_end_a_sentence() -> None:
    text = "Refer to circular No. 12 and para. 4. " * 200 + "(a). The bank shall comply."
    for piece in split_body(text):
        assert not piece.startswith("12 and")
        assert not piece.startswith("4.")


def test_lines_are_boundaries_so_table_rows_stay_whole() -> None:
    rows = "\n".join(f"Tier {i} | {i * 1000} to {i * 2000} | 10 per cent" for i in range(400))
    for piece in split_body(rows):
        for line in piece.splitlines():
            assert line.startswith("Tier ")
            assert line.endswith("10 per cent")


def test_one_sentence_longer_than_the_limit_is_cut_at_whitespace() -> None:
    giant = "word " * 5000
    pieces = split_body(giant.strip())
    assert len(pieces) > 1
    assert all(estimate_tokens(p) <= MAX_CHUNK_TOKENS for p in pieces)
    assert all(p.split() == ["word"] * len(p.split()) for p in pieces)


def test_splitting_is_deterministic() -> None:
    assert split_body(LONG) == split_body(LONG)


def draft(**overrides: object) -> ChunkDraft:
    fields: dict[str, object] = {
        "para_id": f.PARA_ID,
        "sub_index": 0,
        "doc_id": f.DOC_ID,
        "ordinal": 42,
        "body": f.BODY,
        "embed_text": f"{f.TITLE}\n{f.HEADING}\nApplies to: NBFC - Upper Layer\n{f.BODY}",
        "entity_codes": ("nbfc_ul",),
        "valid_from": date(2025, 4, 1),
        "valid_to": None,
        "authority_rank": 30,
    }
    fields.update(overrides)
    return make_draft(**fields)  # type: ignore[arg-type]


def test_a_draft_hashes_its_embed_text_and_round_trips_through_json() -> None:
    d = draft()
    assert d.content_hash == sha256_hex(d.embed_text)
    assert ChunkDraft.from_json(d.to_json()) == d


def test_a_draft_becomes_a_valid_phase_0_chunk_with_its_vector() -> None:
    chunk = draft().to_chunk(f.embedding(3))
    assert chunk.ordinal == 42
    assert chunk.sub_index == 0
    assert chunk.body == f.BODY


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"body": "  "}, "no text"),
        ({"embed_text": "header only"}, "end with the body"),
        ({"valid_to": date(2025, 4, 1)}, "after valid_from"),
        ({"entity_codes": ()}, "at least one entity"),
    ],
)
def test_make_draft_refuses_an_inconsistent_row(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        draft(**overrides)
