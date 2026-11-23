"""The numeric check, normalised so that it refuses wrong numbers and not right ones.

Every case marked ``found while building`` is a false positive or a false negative this check
produced before it was fixed; they are kept so none comes back."""

from __future__ import annotations

import pytest

from extant.agent.numerics import check, quantities


@pytest.mark.parametrize(
    ("claim", "quote"),
    [
        ("A bank must keep 10 per cent.", "shall maintain 10% of"),
        ("within thirty days", "within 30 days of the"),
        ("twenty-five per cent", "25 percent"),
        ("Rs 5 lakh", "up to Rs 5,00,000 per borrower"),
        ("Rs 2 crore", "Rs 2,00,00,000"),
        ("effective from 2026-01-01", "with effect from January 1, 2026"),
        ("from 1 April 2026", "from April 1, 2026"),
        ("within one year", "within a period of 12 months"),  # found while building
        ("within two weeks", "within 14 days"),
        ("50 basis points", "50 bps"),
        ("one hundred and eighty days", "180 days"),
        ("a ratio of 9", "a ratio of 9 per cent"),
    ],
)
def test_the_same_number_in_another_spelling_matches(claim: str, quote: str) -> None:
    assert check(claim, [quote]).passed, quantities(claim)


@pytest.mark.parametrize(
    ("claim", "quote"),
    [
        ("a ratio of 12 per cent", "a minimum CRAR of 9 per cent"),
        ("a ratio of 19 per cent", "a ratio of 9 per cent"),  # found while building, see below
        ("within 30 days", "within one month"),  # a month is not a fixed number of days
        ("from 2026-01-02", "with effect from January 1, 2026"),
        ("10 per cent", "10 days"),
        ("2026", "January 1, 2026"),  # a bare year is not the date
        ("every three months", "report quarterly"),  # found while building: refuses, by design
    ],
)
def test_a_different_number_fails(claim: str, quote: str) -> None:
    assert not check(claim, [quote]).passed


def test_a_paragraph_number_in_the_ignore_list_never_eats_digits_of_a_figure() -> None:
    """Found by the property test: ignoring the cited paragraph's number "1" as a substring
    turned "19 per cent" into "9 per cent", so a wrong figure verified."""
    assert not check("a ratio of 19 per cent", ["a ratio of 9 per cent"], ignore=["1"]).passed
    assert not check("a ratio of 12 per cent", ["ratio of 9 per cent"], ignore=["1", "2"]).passed


@pytest.mark.parametrize(
    "claim",
    [
        "Under paragraph 13, the limit is 15 per cent.",
        "Section 20(1)(b) restricts loans; the limit is 15 per cent.",
        "Circular DOR.ACC.REC.87/21-02-067/2025-26 sets the limit at 15 per cent.",
        "The Zebra Directions, 2025 set the limit at 15 per cent.",
        "Annex II and Chapter III set the limit at 15 per cent.",
    ],
)
def test_numbers_that_name_rather_than_state_are_not_checked(claim: str) -> None:
    assert check(
        claim, ["the limit shall be 15 per cent"], ignore=["Zebra Directions, 2025"]
    ).passed


def test_a_title_the_composer_rewords_is_still_a_false_positive() -> None:
    """Known gap: the ignore list holds the exact title, so "the 2025 Zebra Directions" leaves
    2025 unmatched. Logged, and left as a refusal: the bias toward refusing is deliberate."""
    result = check(
        "The 2025 Zebra Directions set 15 per cent.",
        ["15 per cent"],
        ignore=["Zebra Directions, 2025"],
    )
    assert not result.passed
    assert [str(q) for q in result.unmatched] == ["2025"]


def test_every_failure_is_logged_with_both_strings(caplog: pytest.LogCaptureFixture) -> None:
    check("a ratio of 12 per cent", ["a ratio of 9 per cent"])
    (record,) = caplog.records
    assert "a ratio of 12 per cent" in record.getMessage()
    assert "a ratio of 9 per cent" in record.getMessage()
