"""Authority ranks. A lower number wins.

The two non-binding ranks matter: an answer supported only by an FAQ or a press release
has to say so (a Phase 5 guardrail), and that guardrail is only as right as this ordering.
"""

from __future__ import annotations

from enum import IntEnum


class AuthorityRank(IntEnum):
    ACT = 10
    RULES_AND_REGULATIONS = 20
    MASTER_DIRECTION = 30
    DIRECTION_OR_NOTIFICATION = 40
    CIRCULAR = 50
    FAQ = 60
    PRESS_RELEASE = 70

    @property
    def binding(self) -> bool:
        """Whether an instrument of this rank has legal force."""
        return self not in _NON_BINDING

    @property
    def label(self) -> str:
        return _LABELS[self]

    def outranks(self, other: AuthorityRank) -> bool:
        """True when this rank prevails over ``other`` in a conflict."""
        return self.value < other.value


_NON_BINDING = frozenset({AuthorityRank.FAQ, AuthorityRank.PRESS_RELEASE})

_LABELS = {
    AuthorityRank.ACT: "Act of Parliament",
    AuthorityRank.RULES_AND_REGULATIONS: "Rules and Regulations made under an Act",
    AuthorityRank.MASTER_DIRECTION: "Master Direction",
    AuthorityRank.DIRECTION_OR_NOTIFICATION: "Direction or Notification",
    AuthorityRank.CIRCULAR: "Circular",
    AuthorityRank.FAQ: "FAQ",
    AuthorityRank.PRESS_RELEASE: "Press release",
}
