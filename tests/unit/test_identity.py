from datetime import date

import pytest

from extant.core.identity import (
    InvalidIdentifierError,
    doc_id_of,
    is_schema_name,
    make_block_anchor,
    make_doc_id,
    make_id_anchor,
    make_para_id,
    make_schema_name,
    parse_doc_id,
    parse_para_id,
    parse_schema_name,
    validate_entity_code,
    validate_source_anchor,
)

VALID_DOC_IDS = [
    "rbi:md:2025:0147:v1",
    "rbi:md:2025:0147:v2",
    "rbi:circ:2016:0001:v1",
    "rbi:circ:2016:10234:v12",
    "itact:act:1961:0043:v1",
    "gst:ctrate:2017:0012:v3",
]

MALFORMED_DOC_IDS = [
    "",
    "RBI:md:2025:0147:v1",  # uppercase corpus
    "r:md:2025:0147:v1",  # corpus too short
    "rbi_x:md:2025:0147:v1",  # underscore in corpus
    "rbi:md:25:0147:v1",  # two digit year
    "rbi:md:2025:147:v1",  # serial not padded
    "rbi:md:2025:00147:v1",  # serial over padded, a second spelling of 0147
    "rbi:md:2025:0147:v0",  # version zero
    "rbi:md:2025:0147:v01",  # leading zero version
    "rbi:md:2025:0147:v40000",  # beyond smallint version_seq
    "rbi:md:2025:0147",  # no version
    "rbi:md:2025:0147:1",  # version without v
    "rbi:md:2025:0147:v1\n",  # trailing newline, which a $ anchored regex would accept
    " rbi:md:2025:0147:v1",
    "rbi:md:2025:0147:v1#ch4.3A",  # a para_id is not a doc_id
    "rbi::2025:0147:v1",
]

VALID_PARA_IDS = [
    "rbi:md:2025:0147:v1#ch4.3A",
    "rbi:md:2025:0147:v1#annex2.1",
    "rbi:md:2025:0147:v1#preamble",
    "itact:act:1961:0043:v1#s80C.2.xviii",
]

MALFORMED_PARA_IDS = [
    "rbi:md:2025:0147:v1",  # no anchor
    "rbi:md:2025:0147:v1#",
    "rbi:md:2025:0147:v1#4.3A",  # anchor must open with a kind prefix
    "rbi:md:2025:0147:v1#Ch4.3A",  # kind prefix is lowercase
    "rbi:md:2025:0147:v1#ch4.3A(i)",  # parentheses map to dots
    "rbi:md:2025:0147:v1#ch4..3A",
    "rbi:md:2025:0147:v1#ch4.3A.",
    "rbi:md:2025:0147:v1#ch 4",
    "rbi:md:2025:0147:v1#ch4#3A",
    "rbi:md:2025:147:v1#ch4.3A",  # malformed doc part
    "rbi:md:2025:0147:v1#" + "a" * 121,  # anchor too long
]


@pytest.mark.parametrize("value", VALID_DOC_IDS)
def test_doc_id_round_trips(value: str) -> None:
    parts = parse_doc_id(value)
    assert str(parts) == value
    assert make_doc_id(parts.corpus, parts.instrument, parts.year, parts.serial, parts.version) == (
        value
    )


def test_make_doc_id_pads_the_serial() -> None:
    assert make_doc_id("rbi", "md", 2025, 147, 1) == "rbi:md:2025:0147:v1"
    assert make_doc_id("rbi", "circ", 2016, 10234, 12) == "rbi:circ:2016:10234:v12"


def test_doc_id_base_is_shared_across_versions() -> None:
    assert parse_doc_id("rbi:md:2025:0147:v1").base == parse_doc_id("rbi:md:2025:0147:v2").base


@pytest.mark.parametrize("value", MALFORMED_DOC_IDS)
def test_malformed_doc_id_is_rejected(value: str) -> None:
    with pytest.raises(InvalidIdentifierError):
        parse_doc_id(value)


@pytest.mark.parametrize(
    ("corpus", "instrument", "year", "serial", "version"),
    [
        ("RBI", "md", 2025, 147, 1),
        ("rbi", "md", 25, 147, 1),
        ("rbi", "md", 2025, -1, 1),
        ("rbi", "md", 2025, 147, 0),
        ("rbi", "m d", 2025, 147, 1),
    ],
)
def test_make_doc_id_rejects_bad_components(
    corpus: str, instrument: str, year: int, serial: int, version: int
) -> None:
    with pytest.raises(InvalidIdentifierError):
        make_doc_id(corpus, instrument, year, serial, version)


@pytest.mark.parametrize("value", VALID_PARA_IDS)
def test_para_id_round_trips(value: str) -> None:
    parts = parse_para_id(value)
    assert str(parts) == value
    assert make_para_id(parts.doc_id, parts.anchor) == value


def test_para_id_names_its_document() -> None:
    assert doc_id_of("rbi:md:2025:0147:v1#ch4.3A") == "rbi:md:2025:0147:v1"
    assert make_para_id("rbi:md:2025:0147:v1", "ch4.3A") == "rbi:md:2025:0147:v1#ch4.3A"


@pytest.mark.parametrize("value", MALFORMED_PARA_IDS)
def test_malformed_para_id_is_rejected(value: str) -> None:
    with pytest.raises(InvalidIdentifierError):
        parse_para_id(value)


@pytest.mark.parametrize("anchor", ["", "4.3A", "ch4.3A(i)", "ch 4", "ch4.", "a" * 121])
def test_make_para_id_rejects_bad_anchor(anchor: str) -> None:
    with pytest.raises(InvalidIdentifierError):
        make_para_id("rbi:md:2025:0147:v1", anchor)


# --------------------------------------------------------------------------- schema names


def test_schema_name_accepts_a_real_name() -> None:
    parts = parse_schema_name("idx_rbi_20261118_a3f91c4")
    assert (parts.corpus, parts.build_date, parts.commit) == ("rbi", date(2026, 11, 18), "a3f91c4")
    assert make_schema_name("rbi", date(2026, 11, 18), "a3f91c4") == "idx_rbi_20261118_a3f91c4"


@pytest.mark.parametrize(
    "value",
    [
        "public",
        "",
        "pg_catalog",
        "idx_rbi_20261118_a3f91c4; DROP SCHEMA public CASCADE; --",
        'idx_rbi_20261118_a3f91c4"; DROP SCHEMA public CASCADE; --',
        "idx_rbi_20261118_a3f91c4 ",
        "idx_rbi_20261118_a3f91c4\n",
        "IDX_RBI_20261118_A3F91C4",
        "idx_rbi_20261118_A3F91C4",  # commit must be lowercase hex
        "idx_rbi_20261340_a3f91c4",  # impossible date
        "idx_rbi_20260230_a3f91c4",
        "idx_rbi_2026111_a3f91c4",
        "idx_rbi_20261118_a3f9",  # commit too short
        "idx_rbi_20261118_g3f91c4",  # not hex
        "idx_rbi_x_20261118_a3f91c4",  # underscore in corpus
        "idx__20261118_a3f91c4",
        "idx_rbi_20261118_" + "a" * 50,  # beyond the 63 byte identifier limit
    ],
)
def test_schema_name_rejects_anything_else(value: str) -> None:
    assert not is_schema_name(value)
    with pytest.raises(InvalidIdentifierError):
        parse_schema_name(value)


def test_entity_codes() -> None:
    for code in ("nbfc_ul", "sfb", "ucb", "aifi"):
        assert validate_entity_code(code) == code
    for bad in ("", "NBFC_UL", "nbfc-ul", "_sfb", "sfb_", "nbfc__ul", "1sfb"):
        with pytest.raises(InvalidIdentifierError):
            validate_entity_code(bad)


@pytest.mark.parametrize("anchor", ["#F9", "#C3K", "#ch4.3A", "#a_b-c:d", "block:0", "block:212"])
def test_source_anchors_accepted(anchor: str) -> None:
    assert validate_source_anchor(anchor) == anchor


@pytest.mark.parametrize(
    "anchor",
    ["", "F9", "#", "# F9", "#F9 ", "#F#9", "#-F9", "block:", "block:-1", "block:01",
     "block:1a", "page:3", "#" + "a" * 129, "block:12\n"],
)  # fmt: skip
def test_malformed_source_anchors_rejected(anchor: str) -> None:
    with pytest.raises(InvalidIdentifierError):
        validate_source_anchor(anchor)


def test_anchor_constructors() -> None:
    assert make_id_anchor("F9") == "#F9"
    assert make_block_anchor(212) == "block:212"
    with pytest.raises(InvalidIdentifierError):
        make_block_anchor(-1)
    with pytest.raises(InvalidIdentifierError):
        make_id_anchor("has space")
