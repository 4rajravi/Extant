from itertools import pairwise

from extant.core.authority import AuthorityRank

EXPECTED = [
    (AuthorityRank.ACT, 10, True),
    (AuthorityRank.RULES_AND_REGULATIONS, 20, True),
    (AuthorityRank.MASTER_DIRECTION, 30, True),
    (AuthorityRank.DIRECTION_OR_NOTIFICATION, 40, True),
    (AuthorityRank.CIRCULAR, 50, True),
    (AuthorityRank.FAQ, 60, False),
    (AuthorityRank.PRESS_RELEASE, 70, False),
]


def test_ranks_match_the_table() -> None:
    assert [(r, r.value, r.binding) for r in AuthorityRank] == EXPECTED


def test_lower_number_wins() -> None:
    ranks = list(AuthorityRank)
    assert ranks == sorted(ranks)
    for stronger, weaker in pairwise(ranks):
        assert stronger.outranks(weaker)
        assert not weaker.outranks(stronger)
    assert not AuthorityRank.CIRCULAR.outranks(AuthorityRank.CIRCULAR)
    assert min(AuthorityRank.PRESS_RELEASE, AuthorityRank.ACT) is AuthorityRank.ACT


def test_faq_and_press_release_are_not_binding() -> None:
    assert not AuthorityRank.FAQ.binding
    assert not AuthorityRank.PRESS_RELEASE.binding
    # Every binding rank outranks every non-binding one.
    binding = [r for r in AuthorityRank if r.binding]
    non_binding = [r for r in AuthorityRank if not r.binding]
    assert max(binding) < min(non_binding)


def test_labels() -> None:
    assert AuthorityRank.ACT.label == "Act of Parliament"
    assert AuthorityRank.PRESS_RELEASE.label == "Press release"
