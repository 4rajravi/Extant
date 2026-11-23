"""Redaction at admission: every Indian identifier format, replaced by its type token."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from extant.guards.pii import KINDS, found, notice, redact


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("My PAN is ABCPE1234F, can we lend?", "My PAN is <PAN>, can we lend?"),
        ("pan abcpe1234f", "pan <PAN>"),
        ("GSTIN 27ABCPE1234F1Z5 is registered", "GSTIN <GSTIN> is registered"),
        ("Aadhaar 2345 6789 0123 is linked", "Aadhaar <AADHAAR> is linked"),
        ("aadhaar 234567890123", "aadhaar <AADHAAR>"),
        ("account no. 001234567890 at SBIN0001234", "account no. <ACCOUNT> at <IFSC>"),
        ("our a/c 50100123456789 was frozen", "our a/c <ACCOUNT> was frozen"),
        ("bank account number: 1234 5678 9012 34", "bank account number: <ACCOUNT>"),
        ("call +91 98765 43210 or 9876543210", "call <MOBILE> or <MOBILE>"),
        ("CIN L17110MH1973PLC019786", "CIN <CIN>"),
        ("write to a.person@example.co.in", "write to <EMAIL>"),
        ("card 4111 1111 1111 1111", "card <ID_NUMBER>"),
    ],
)
def test_each_identifier_becomes_its_type_token(text: str, expected: str) -> None:
    out = redact(text)
    assert out.text == expected
    assert found(out.text) == []


@pytest.mark.parametrize(
    "text",
    [
        "Is circular DBOD.No.BP.BC.12/21.04.048/2014-15 still in force?",
        "What replaced DOR.ACC.REC.87/21-02-067/2025-26?",
        "RBI/2025-26/123 dated 01.04.2025 for Rs 500 crore",
        "a limit of 15 per cent of 10000000000 rupees",
        "Write to cisbi@rbi.org.in for a clarification",  # the regulator's own mailbox
        "paragraph rbi:md:2025:0387:v1#annex3 of the 2026 Directions",
        "between 1 April 2025 and 31 March 2026, 9,876 accounts",
    ],
)
def test_references_amounts_dates_and_ids_are_left_alone(text: str) -> None:
    assert redact(text).text == text


def test_the_outer_identifier_is_replaced_before_its_parts() -> None:
    # A GSTIN contains a PAN; replacing the PAN first would leave "27<PAN>1Z5".
    assert redact("27ABCPE1234F1Z5").kinds == ("GSTIN",)


def test_the_notice_names_what_was_removed() -> None:
    out = redact("PAN ABCPE1234F, mobile 9876543210")
    assert out.kinds == ("PAN", "MOBILE")
    text = notice(out.kinds)
    assert text is not None
    assert "a PAN and a mobile number" in text
    assert notice(()) is None


def test_a_hex_digest_is_not_read_as_a_phone_number() -> None:
    # The log carries the query's sha256; its digit runs are glued to letters.
    assert found("9f2c3a8b9876543210ffe1234567890123ab") == []


_ID = st.sampled_from(
    [
        "ABCPE1234F",
        "27ABCPE1234F1Z5",
        "2345 6789 0123",
        "9876543210",
        "+91 98765 43210",
        "SBIN0001234",
        "L17110MH1973PLC019786",
        "x.y@mail.example.com",
    ]
)
_WORDS = st.sampled_from(["the", "bank", "capital", "of", "15 per cent", "2026", "a/c", "rule"])


@settings(max_examples=500)
@given(st.lists(st.one_of(_ID, _WORDS), min_size=1, max_size=12))
def test_redaction_leaves_nothing_the_detector_finds_and_is_idempotent(parts: list[str]) -> None:
    text = " ".join(parts)
    once = redact(text)
    assert found(once.text) == []
    assert redact(once.text).text == once.text
    assert set(once.kinds) <= set(KINDS)
    assert all(token not in json.dumps(once.text) for token in ("ABCPE1234F", "9876543210"))
