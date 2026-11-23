"""Answer level ablation arms: switches only the Phase 6 harness turns.

Serving always runs ``BASELINE``. ``evals.answers`` builds the other arms to measure what
each guard buys, and a test holds that nothing under ``extant/`` constructs an arm other than
the default. Each switch is read in exactly the places its guard lives:

- ``supersession=False`` removes supersession handling everywhere it runs: retrieval's date
  filter (the ranked search spans every version), the gate's date drops (entity scope still
  applies), the per claim supersession check, its post check, and the state validator that
  forbids composing over a paragraph out of force. What is left is the system without any
  notion of "as at the date asked".
- ``verification=False`` removes the checks between the composer and the answer: no claim
  check, no judge, no post check in ``finalize``. Claims carry what the composer emitted.
- ``retry=True`` adds the bounded retry Phase 4 left out: after a recoverable verification
  failure (a quote, a number or an internal label), one more composer call with the failure
  as context, checked again. Phase 6 measures whether it earns its cost.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Arm:
    name: str = "baseline"
    supersession: bool = True
    verification: bool = True
    retry: bool = False


BASELINE = Arm()
