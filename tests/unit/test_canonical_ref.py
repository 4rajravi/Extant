"""The reference normaliser, tested on real strings only.

The table below pairs surface forms observed in RBI's published lists with the canonical form
the rules give, worked out by hand from the rules in ``extant.core.identity``. The property
tests run over every distinct form RBI published (``reference_surface_forms.txt``).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from extant.core.identity import (
    InvalidReferenceError,
    canonical_ref,
    ref_variants,
    withdrawal_key,
)

FORMS = Path(__file__).resolve().parents[1] / "fixtures" / "rbi" / "reference_surface_forms.txt"
NOT_REFERENCES = {"Mail Box Clarification", "D.O. Letter"}
# Real forms from outside the three lists: the spec's documented example, and a reference
# printed inside the consolidated directions' own text.
SEEN_ELSEWHERE = {
    "DBOD.No.BP.BC.1/21.04.048/2015-16",
    "FIDD.MSME & NFS.BC.No.3/06.02.31/2020-21",
}


def surface_forms() -> list[str]:
    lines = FORMS.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line and not line.startswith("#")]


# (raw as published, canonical): each raw string occurs in RBI's lists or SEEN_ELSEWHERE.
CASES = [
    # the documented example
    ("DBOD.No.BP.BC.1/21.04.048/2015-16", "DBOD.BP.BC.1/21.04.048/2015-16"),
    # case: DoR and DOR are one department
    ("DoR.FIN.REC.No.45/03.10.119/2023-24", "DOR.FIN.REC.45/03.10.119/2023-24"),
    ("DoR(NBFC).PD.003/03.10.119/2016-17", "DOR(NBFC).PD.003/03.10.119/2016-17"),
    # "No." spellings
    ("DBOD.NO.PSBD.BC.62/16.13.100/2013-14", "DBOD.PSBD.BC.62/16.13.100/2013-14"),
    ("DBR.Appt.No:9/29.67.001/2019-20", "DBR.APPT.9/29.67.001/2019-20"),
    ("UBD.BSD.No.IP.30/12.05.05/2002-03", "UBD.BSD.IP.30/12.05.05/2002-03"),
    ("UBD.No.BSD.No.IP.30/12.05.05/2002-03", "UBD.BSD.IP.30/12.05.05/2002-03"),
    # a dot before "(" goes, so "UBD.(PCB)" and "UBD (PCB)" meet
    ("No.UBD.(PCB)17/DC.PR.474-90/91", "UBD(PCB)17/DC.PR.474-90/91"),
    # a department code glued to "No."
    ("RPCDNo.RF.DIR.BC.26/D.2-87/88", "RPCD.RF.DIR.BC.26/D.2-87/88"),
    # spaces: between words a dot, elsewhere nothing
    ("DOR.SIG.FIN.REC 8/26.03.001/2023-24", "DOR.SIG.FIN.REC.8/26.03.001/2023-24"),
    ("DBOD No.BP.BC.10/21.04.048/2004-05", "DBOD.BP.BC.10/21.04.048/2004-05"),
    ("UBD.BPD (PCB)MC.No.13/13.05.000/2006-07", "UBD.BPD(PCB)MC.13/13.05.000/2006-07"),
    ("FIDD.MSME & NFS.BC.No.3/06.02.31/2020-21", "FIDD.MSME&NFS.BC.3/06.02.31/2020-21"),
    ("DoR.SOG (DEA Fund) No.37/30.01.002/2025-26", "DOR.SOG(DEA.FUND).37/30.01.002/2025-26"),
    # descriptive prefixes
    ("Notification No. DFC.55/DG(O)-87", "DFC.55/DG(O)-87"),
    ("Notification DFC.55/DG (O)-87", "DFC.55/DG(O)-87"),
    ("Notification No. DFC. 119/DG(SPT)-98", "DFC.119/DG(SPT)-98"),
    ("Master Direction DBR.Dir.No.85/13.03.00/2015-16", "DBR.DIR.85/13.03.00/2015-16"),
    ("Master Direction/DBR.FSD.No.101/24.01.041/2015-16", "DBR.FSD.101/24.01.041/2015-16"),
    ("RBI/2016-17/DBR.FID.No.1/01.02.000/2016-17", "DBR.FID.1/01.02.000/2016-17"),
    # a bare RBI serial is the reference
    ("RBI/DOR/2025-26/269", "RBI/DOR/2025-26/269"),
    # demi-official letters keep their D.O. prefix: they are a different instrument
    ("D.O.DBOD.No.Leg.96/09.07.007/2004-05", "D.O.DBOD.LEG.96/09.07.007/2004-05"),
    ("D.O.No.DBOD.BP.BC.53/C.473-91", "D.O.DBOD.BP.BC.53/C.473-91"),
    # typographic accidents
    ("RPCD.No.RRB.BC.35/03.05.33\u00a9/2008-09", "RPCD.RRB.BC.35/03.05.33(C)/2008-09"),
    ("RPCD.No.PLNFS.BC.4\\06.11.02\\97-98", "RPCD.PLNFS.BC.4/06.11.02/97-98"),
    ("lECD.No.2046/04.02.02B/2001-02", "IECD.2046/04.02.02B/2001-02"),
    ("DNBS.(PD):261/2013-14", "DNBS(PD).261/2013-14"),
    ("DBODNo.GC.BC-87/C..408A(88)-85", "DBOD.GC.BC-87/C.408A(88)-85"),
    # a circular carrying three numbers keeps them all
    ("DBOD.No.Nat.152, 153, 154/C.464(M)72", "DBOD.NAT.152,153,154/C.464(M)72"),
]


@pytest.mark.parametrize(("raw", "canonical"), CASES)
def test_canonical_form_of_real_references(raw: str, canonical: str) -> None:
    assert raw in set(surface_forms()) or raw in SEEN_ELSEWHERE
    assert canonical_ref(raw) == canonical


# Pairs that must stay apart: different circulars, or a difference the rules refuse to guess.
DISTINCT = [
    # a financial year suffix differs: RBI's lists disagree, and only one can be right
    ("DOR.MRG.REC.80/00-00-003/2022-23", "DOR.MRG.REC.80/00-00-003/2023-24"),
    # a renamed department is a different series
    ("DBOD.No.BP.BC.1/21.04.048/2015-16", "DBR.No.BP.BC.1/21.04.048/2015-16"),
    # leading zeros are not stripped
    ("DoR.STR.REC 07/09.27.000/2025-26", "DoR.STR.REC.7/09.27.000/2025-26"),
    # a demi-official letter is not the circular of the same number
    ("D.O.DBOD.No.Leg.96/09.07.007/2004-05", "DBOD.No.Leg.96/09.07.007/2004-05"),
]


@pytest.mark.parametrize(("a", "b"), DISTINCT)
def test_different_references_stay_apart(a: str, b: str) -> None:
    assert canonical_ref(a) != canonical_ref(b)


@pytest.mark.parametrize("raw", sorted(NOT_REFERENCES))
def test_non_references_fail_loudly(raw: str) -> None:
    with pytest.raises(InvalidReferenceError, match="not a circular reference"):
        canonical_ref(raw)


def test_every_published_form_normalises_or_is_a_known_non_reference() -> None:
    failed = set()
    for raw in surface_forms():
        try:
            canonical_ref(raw)
        except InvalidReferenceError:
            failed.add(raw)
    assert failed == NOT_REFERENCES


def test_canonical_forms_are_idempotent_uppercase_and_spaceless() -> None:
    for raw in surface_forms():
        if raw in NOT_REFERENCES:
            continue
        key = canonical_ref(raw)
        assert canonical_ref(key) == key, raw
        assert key == key.upper(), raw
        assert not any(ch.isspace() for ch in key), raw


def test_ref_variants_round_trip_for_every_published_form() -> None:
    for raw in surface_forms():
        if raw in NOT_REFERENCES:
            continue
        key = canonical_ref(raw)
        variants = ref_variants(key)
        assert variants[0] == key
        assert all(canonical_ref(v) == key for v in variants), (raw, variants)


def test_ref_variants_of_the_documented_example() -> None:
    assert ref_variants("DBOD.BP.BC.1/21.04.048/2015-16") == [
        "DBOD.BP.BC.1/21.04.048/2015-16",
        "DBOD.No.BP.BC.1/21.04.048/2015-16",
        "DBOD.BP.BC.No.1/21.04.048/2015-16",
    ]
    assert "DoR.FIN.REC.45/03.10.119/2023-24" in ref_variants("DOR.FIN.REC.45/03.10.119/2023-24")


def test_ref_variants_refuses_a_non_canonical_input() -> None:
    with pytest.raises(InvalidReferenceError, match="not a canonical reference"):
        ref_variants("DBOD.No.BP.BC.1/21.04.048/2015-16")


def test_withdrawal_key_separates_what_the_list_separates() -> None:
    # Two rows RBI lists under one number and date, as amendments of one notification.
    a = withdrawal_key(
        "DFC.118/DG(SPT)-98", date(1998, 1, 31), "Amended upto 01.10.2002 - NBFC Acceptance"
    )
    b = withdrawal_key(
        "DFC.118/DG(SPT)-98", date(1998, 1, 31), "Amended upto 30.06.2005 - Schedules"
    )
    assert a != b
    assert len(a) == 64
    assert a == withdrawal_key(
        "DFC.118/DG(SPT)-98", date(1998, 1, 31), "Amended upto 01.10.2002 - NBFC Acceptance"
    )
    # rows with no reference are told apart by date and title
    m1 = withdrawal_key(None, date(2018, 6, 20), "Clarification on A")
    m2 = withdrawal_key(None, date(2018, 6, 20), "Clarification on B")
    assert m1 != m2
    with pytest.raises(InvalidReferenceError, match="not a canonical reference"):
        withdrawal_key("DBOD.No.BP.BC.1/21.04.048/2015-16", date(2015, 7, 1), "t")
