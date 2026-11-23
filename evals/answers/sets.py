"""The answer level question sets, and the evidence that each unanswerable question is one.

Three sets, never pooled:

- ``answerable``: natural questions written by hand from a seeded sample of in-force
  paragraphs (one per direction, spread across entity types), so the gold paragraph is known
  by construction. This replaces the FAQ derived set: RBI's FAQ answers cite no provisions,
  and the decision (23 November 2026) was to write questions from gold paragraphs instead.
  The questions were written from the paragraph alone; no gold answer appears in any
  prompt, fixture or cassette.
- ``as_of``: five amended provisions, each asked with the same words on the last day of the
  old version and the first day of the new, the gold being the version in force that day.
- ``unanswerable``: ten questions from each of four sources: topics absent from the corpus,
  entity types no provision on the topic covers, dates before the governing instrument
  existed, and provisions repealed by the date asked. Each carries why it is unanswerable
  and a retrieval check of what the index does and does not contain. An entry whose check
  does not bear out its reason fails validation and cannot be in the set.

    uv run python -m evals.answers.sets check     # runs the checks, writes evals/answers/sets/

The split is by direction (answerable, as_of) or by question (unanswerable), half dev and
half test, decided by a hash so it never moves.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

SETS_DIR = Path(__file__).resolve().parent / "sets"
TODAY = date(2026, 11, 20)  # every answerable question is asked as of this date
BEFORE = date(2025, 6, 30)  # before the consolidated directions came into force

SetName = Literal["answerable", "as_of", "unanswerable"]
Source = Literal["absent", "entity", "before", "repealed", "withdrawn"]


class SetValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Question:
    qid: str
    set: SetName
    text: str
    entity: str | None
    as_of: date
    gold: tuple[str, ...] = ()  # para_ids; answerable and as_of only
    group: str = ""  # what the split is decided on
    source: Source | None = None  # unanswerable only
    why: str = ""  # unanswerable only: why no answer exists
    terms: str = ""  # unanswerable: the regex the retrieval check searches for
    expect: str | None = None  # unanswerable: the refusal reason expected first

    @property
    def key(self) -> str:
        return hashlib.sha256(f"phase6:{self.group or self.qid}".encode()).hexdigest()

    @property
    def split(self) -> str:
        return _SPLITS[(self.set, self.group or self.qid)]


def _a(qid: str, entity: str, gold: str, text: str) -> Question:
    return Question(qid, "answerable", text, entity, TODAY, (gold,), gold.split(":v")[0])


ANSWERABLE: tuple[Question, ...] = (
    _a(
        "a00",
        "sfb",
        "rbi:md:2025:0185:v6#p81",
        "For an infrastructure project under the PPP model, when may a small finance bank begin disbursing the loan?",
    ),
    _a(
        "a01",
        "ucb",
        "rbi:md:2025:0269:v1#p1",
        "Is RBI considering proposals to organise new urban co-operative banks or to convert credit societies into them?",
    ),
    _a(
        "a02",
        "lab",
        "rbi:md:2025:0246:v4#p13",
        "A depositor asks a local area bank for an unclaimed deposit that was already transferred to the Fund. What must the bank do?",
    ),
    _a(
        "a03",
        "pb",
        "rbi:md:2025:0382:v1#p7.2",
        "A payments bank approved for mobile banking now wants to launch another digital banking channel. What must it do, and within what time must it send the audit report to RBI?",
    ),
    _a(
        "a04",
        "nbfc_ml",
        "rbi:md:2026:0460:v1#p32",
        "What must the compliance management solution used by a middle layer NBFC be able to do?",
    ),
    _a(
        "a05",
        "ucb",
        "rbi:md:2026:0441:v1#p24",
        "After an audit firm finishes a term as statutory auditor of an urban co-operative bank, how long before the same bank can appoint it again?",
    ),
    _a(
        "a07",
        "aifi",
        "rbi:md:2025:0329:v2#p76",
        "Can an all India financial institution invest in non-government securities with an original maturity under one year?",
    ),
    _a(
        "a08",
        "rcb",
        "rbi:md:2025:0302:v2#p106",
        "How is a guarantee given by a rural co-operative bank for another lender's non-fund based facility treated for exposure and capital purposes?",
    ),
    _a(
        "a09",
        "cb",
        "rbi:md:2025:0144:v2#p15",
        "Can the Indian wholly owned subsidiary of a foreign bank reduce the parent's stake and list on a stock exchange?",
    ),
    _a(
        "a10",
        "cb",
        "rbi:md:2025:0157:v4#p55",
        "What oversight must a commercial bank's country risk management process include?",
    ),
    _a(
        "a11",
        "ucb",
        "rbi:md:2025:0274:v7#p31",
        "By when must a scheduled urban co-operative bank submit its fortnightly Form B return?",
    ),
    _a(
        "a12",
        "rcb",
        "rbi:md:2025:0295:v1#p8",
        "How must notices of the shareholders' meeting to approve an amalgamation of rural co-operative banks be published?",
    ),
    _a(
        "a13",
        "nbfc_spd",
        "rbi:md:2025:0366:v2#p143",
        "May a standalone primary dealer open demat accounts with a depository participant in addition to its accounts with RBI?",
    ),
    _a(
        "a14",
        "pb",
        "rbi:md:2025:0388:v1#p11",
        "Within how long after declaring a dividend must a payments bank report it to RBI?",
    ),
    _a(
        "a15",
        "cb",
        "rbi:md:2025:0153:v4#p30",
        "When the claimants of a deceased depositor's NRE term deposit are residents, how is interest paid after maturity?",
    ),
    _a(
        "a16",
        "nbfc_mfi",
        "rbi:md:2025:0371:v1#p20",
        "What risk weight should an NBFC-MFI assign to loans guaranteed under CGTMSE?",
    ),
    _a(
        "a17",
        "nbfc_aa",
        "rbi:md:2025:0368:v1#p29",
        "If a regulated entity joins the account aggregator ecosystem as a financial information user, does it also have to join as a financial information provider?",
    ),
    _a(
        "a18",
        "ucb",
        "rbi:md:2026:0442:v1#p18",
        "Can an urban co-operative bank file its supervisory returns by email?",
    ),
    _a(
        "a19",
        "rrb",
        "rbi:md:2025:0254:v1#p17",
        "How is a company's liability on business cards treated in a regional rural bank's exposure and asset classification norms?",
    ),
    _a(
        "a20",
        "lab",
        "rbi:md:2025:0238:v3#p9",
        "What is a local area bank's board required to do when asset quality deteriorates?",
    ),
    _a(
        "a21",
        "rrb",
        "rbi:md:2025:0260:v2#p49",
        "How should a regional rural bank value its AFS and HFT securities, and what happens to any net appreciation?",
    ),
    _a(
        "a26",
        "nbfc_ml",
        "rbi:md:2026:0463:v1#p9",
        "Who in an NBFC is responsible for implementing its fraud risk management policy, and what must they place before the board?",
    ),
    _a(
        "a27",
        "rrb",
        "rbi:md:2025:0265:v2#p4",
        "In what form must a regional rural bank prepare its balance sheet and profit and loss account?",
    ),
    _a(
        "a28",
        "nbfc_bl",
        "rbi:md:2025:0354:v2#p22",
        "When may an NBFC recognise dividend income from shares on an accrual basis?",
    ),
    _a(
        "a29",
        "nbfc",
        "rbi:md:2026:0468:v1#p9",
        "What must an auditor state in the report of a company that RBI has advised does not need a certificate of registration?",
    ),
    _a(
        "a30",
        "sfb",
        "rbi:md:2025:0181:v7#p18",
        "On which dates does a small finance bank work out the split of its savings deposits into demand and time liabilities?",
    ),
    _a(
        "a31",
        "cb",
        "rbi:md:2026:0411:v2#p28",
        "What approach must a commercial bank take when developing digital payment products?",
    ),
    _a(
        "a33",
        "rcb",
        "rbi:md:2025:0307:v1#p43",
        "When a rural co-operative bank sells a stressed loan to an ARC for less than its net book value, how is the shortfall treated?",
    ),
    _a(
        "a36",
        "sfb",
        "rbi:md:2025:0197:v1#p17",
        "Must a small finance bank report guarantors who fail to honour an invoked guarantee to credit information companies?",
    ),
    _a(
        "a37",
        "cb",
        "rbi:md:2025:0160:v1#p13",
        "How must a commercial bank that originates a securitisation hold its minimum retention requirement?",
    ),
    _a(
        "a38",
        "cb",
        "rbi:md:2025:0171:v1#p51",
        "If a commercial bank outsources the servicing of microfinance loans, who is responsible for handling customer grievances?",
    ),
    _a(
        "a40",
        "ucb",
        "rbi:md:2026:0443:v1#p20",
        "What should an urban co-operative bank do to promote the nomination facility among customers?",
    ),
    _a(
        "a42",
        "sfb",
        "rbi:md:2026:0422:v2#p15",
        "What must a small finance bank provide its internal audit department with?",
    ),
    _a(
        "a43",
        "cb",
        "rbi:md:2025:0154:v6#p27",
        "Within what overdue period must a bank invoke a default loss guarantee, and how long must the DLG agreement stay in force?",
    ),
    _a(
        "a44",
        "ucb",
        "rbi:md:2026:0437:v1#p21",
        "What fields must an urban co-operative bank's information asset inventory register contain?",
    ),
    _a(
        "a45",
        "sfb",
        "rbi:md:2025:0180:v2#p17",
        "What are the main roles of a small finance bank's board?",
    ),
    _a(
        "a47",
        "aifi",
        "rbi:md:2025:0332:v3#p100",
        "Which guidelines govern an AIFI's resolution of a fresh credit event in a project loan after October 2025?",
    ),
    _a(
        "a48",
        "arc",
        "rbi:md:2025:0374:v2#p97",
        "When must an asset reconstruction company classify a non-performing asset as a loss asset?",
    ),
    _a(
        "a50",
        "arc",
        "rbi:md:2025:0376:v1#p9",
        "Who is responsible for the accuracy of defaulter information an ARC reports, and how should it check directors' details?",
    ),
    _a(
        "a51",
        "cic",
        "rbi:md:2025:0379:v1#p40",
        "How should a credit information company decide how often to review its outsourcing service providers?",
    ),
    _a(
        "a54",
        "nbfc_mgc",
        "rbi:md:2025:0369:v2#p38",
        "Into which classes must a mortgage guarantee company classify its assets?",
    ),
    _a(
        "a55",
        "nbfc_hfc",
        "rbi:md:2025:0365:v5#p88",
        "What must a housing finance company record on fixed deposit receipts about nomination?",
    ),
    _a(
        "b00",
        "pb",
        "rbi:md:2025:0215:v1#p43",
        "What limits apply to a payments bank's net cumulative negative liquidity mismatches in the buckets up to 30 days?",
    ),
    _a(
        "b01",
        "sfb",
        "rbi:md:2026:0419:v2#p150",
        "How often must a small finance bank carry out vulnerability assessment and penetration testing on critical systems?",
    ),
    _a(
        "b02",
        "aifi",
        "rbi:md:2025:0323:v4#p96",
        "When must an AIFI's drawn partial credit enhancement be repaid, and when is it treated as an NPA?",
    ),
    _a(
        "b03",
        "sfb",
        "rbi:md:2026:0417:v1#p58",
        "Is there an age limit for appointing a chief compliance officer at a small finance bank?",
    ),
    _a(
        "b05",
        "sfb",
        "rbi:md:2025:0182:v8#p122",
        "How recently must a rating agency have reviewed a rating for a small finance bank to use it for risk weights?",
    ),
    _a(
        "b06",
        "cb",
        "rbi:md:2025:0150:v7#p25",
        "What minimum statutory liquidity ratio must a commercial bank maintain?",
    ),
    _a(
        "b08",
        "pb",
        "rbi:md:2025:0212:v2#p8",
        "Can a payments bank pay a different interest rate on savings balances above one lakh rupees?",
    ),
    _a(
        "b09",
        "sfb",
        "rbi:md:2025:0189:v4#p29",
        "How much of another company's shares may a small finance bank hold?",
    ),
    _a(
        "b10",
        "ucb",
        "rbi:md:2025:0272:v3#p31",
        "What does a scheduled urban co-operative bank need before it can offer online share trading to its demat customers?",
    ),
    _a(
        "b12",
        "nbfc",
        "rbi:md:2025:0373:v2#p45",
        "After how many days overdue is an NBFC's loan classified as non-performing?",
    ),
    _a(
        "b13",
        "sfb",
        "rbi:md:2025:0196:v3#p41",
        "Within how long must a resolution plan for a stressed account be implemented under a small finance bank's prudential framework?",
    ),
    _a(
        "b14",
        "ucb",
        "rbi:md:2025:0276:v4#p31",
        "How often is interest paid on FCNR(B) deposits at an urban co-operative bank?",
    ),
    _a(
        "b16",
        "cb",
        "rbi:md:2025:0145:v2#p10",
        "When RBI refers a proposed acquisition of a commercial bank's shares to the bank, how quickly must the bank respond?",
    ),
    _a(
        "b18",
        "nbfc_d",
        "rbi:md:2025:0359:v4#p9",
        "By when must an NBFC finalise its annual balance sheet, and what if it wants to extend the balance sheet date?",
    ),
)


def _pair(
    qid: str, entity: str, text: str, old: tuple[date, str], new: tuple[date, str]
) -> list[Question]:
    group = "pair:" + old[1].split(":v")[0] + "#" + old[1].split("#")[1]
    return [
        Question(f"{qid}-old", "as_of", text, entity, old[0], (old[1],), group),
        Question(f"{qid}-new", "as_of", text, entity, new[0], (new[1],), group),
    ]


AS_OF: tuple[Question, ...] = (
    *_pair(
        "t1",
        "nbfc",
        "How are NBFCs identified for the upper layer?",
        (date(2026, 6, 23), "rbi:md:2025:0339:v1#p24"),
        (date(2026, 6, 24), "rbi:md:2025:0339:v2#p24"),
    ),
    *_pair(
        "t2",
        "pb",
        "What minimum share of its required CRR must a payments bank maintain on every day of the reporting fortnight?",
        (date(2026, 10, 8), "rbi:md:2025:0210:v3#p10"),
        (date(2026, 10, 9), "rbi:md:2025:0210:v4#p10"),
    ),
    *_pair(
        "t3",
        "nbfc",
        "By how much may an infrastructure finance company exceed the exposure limit for a group of connected counterparties?",
        (date(2026, 6, 23), "rbi:md:2025:0351:v3#p35"),
        (date(2026, 6, 24), "rbi:md:2025:0351:v4#p35"),
    ),
    *_pair(
        "t4",
        "rrb",
        "How must a regional rural bank build up its investment fluctuation reserve?",
        (date(2026, 5, 17), "rbi:md:2025:0260:v1#p104"),
        (date(2026, 5, 18), "rbi:md:2025:0260:v2#p104"),
    ),
    *_pair(
        "t5",
        "sfb",
        "What minimum share of its required CRR must a small finance bank maintain on each day of the fortnight?",
        (date(2026, 10, 8), "rbi:md:2025:0181:v6#p10"),
        (date(2026, 10, 9), "rbi:md:2025:0181:v7#p10"),
    ),
)


def _u(
    qid: str,
    source: Source,
    entity: str,
    text: str,
    terms: str,
    why: str,
    as_of: date = TODAY,
    expect: str = "no_support_found",
) -> Question:
    return Question(qid, "unanswerable", text, entity, as_of, (), qid, source, why, terms, expect)


_ABSENT = "the RBI corpus holds no provision on this topic"
_ENTITY = "the topic is regulated for other entity types, but no provision covers this entity type"
_BEFORE = "asked as of 30 June 2025, before the consolidated direction governing it came into force"
_DELETED = "the only provision on this was deleted from its direction before the date asked"
_WITHDRAWN = (
    "the circular was withdrawn before the date asked and was not consolidated into a direction"
)

UNANSWERABLE: tuple[Question, ...] = (
    _u(
        "u01",
        "absent",
        "cb",
        "What is the maximum a bank may lend against a racehorse as collateral?",
        r"race ?horse",
        _ABSENT,
    ),
    _u(
        "u02",
        "absent",
        "cb",
        "How must a bank value a professional football club franchise pledged as security?",
        r"football",
        _ABSENT,
    ),
    _u(
        "u03",
        "absent",
        "cb",
        "What capital charge applies to carbon credits a bank trades on its own account?",
        r"carbon credit",
        _ABSENT,
    ),
    _u(
        "u04",
        "absent",
        "cb",
        "What loan to value applies to a bank loan secured by a vintage car?",
        r"vintage|classic car",
        _ABSENT,
    ),
    _u(
        "u05",
        "absent",
        "cb",
        "What KYC applies when a bank opens an account for a space tourism company?",
        r"space tourism",
        _ABSENT,
    ),
    _u(
        "u06",
        "absent",
        "cb",
        "What exposure limit applies to a bank's lending to esports betting platforms?",
        r"e-?sports|betting",
        _ABSENT,
    ),
    _u(
        "u07",
        "absent",
        "cb",
        "How should a bank provision loans secured on patents for genetically modified seeds?",
        r"genetically modified",
        _ABSENT,
    ),
    _u(
        "u08",
        "absent",
        "cb",
        "How much may a bank lend against a yacht taken as security?",
        r"\myachts?\M",
        _ABSENT,
    ),
    _u(
        "u09",
        "absent",
        "cb",
        "What loan to value applies to a bank loan against a vintage wine collection?",
        r"\mwines?\M",
        _ABSENT,
    ),
    _u(
        "u10",
        "absent",
        "cb",
        "May a bank issue deposits backed by NFTs?",
        r"\mNFTs?\M|non-fungible",
        _ABSENT,
    ),
    _u(
        "u11",
        "entity",
        "arc",
        "What loan to value ratio applies to gold loans given by an asset reconstruction company?",
        r"gold loan|gold jewellery|gold ornament",
        _ENTITY,
    ),
    _u(
        "u12",
        "entity",
        "cic",
        "What cash reserve ratio must a credit information company maintain?",
        r"cash reserve ratio|\mCRR\M",
        _ENTITY,
    ),
    _u(
        "u13",
        "entity",
        "nbfc_aa",
        "What cash reserve ratio must an account aggregator maintain?",
        r"cash reserve ratio|\mCRR\M",
        _ENTITY,
    ),
    _u(
        "u14",
        "entity",
        "nbfc_mgc",
        "What interest rate rules apply to savings accounts held at a mortgage guarantee company?",
        r"savings (bank )?(deposit|account)",
        _ENTITY,
    ),
    _u(
        "u15",
        "entity",
        "cic",
        "What deposit insurance premium does a credit information company pay?",
        r"deposit insurance",
        _ENTITY,
    ),
    _u(
        "u16",
        "entity",
        "nbfc_nofhc",
        "How must a non-operative financial holding company price microfinance loans?",
        r"microfinance",
        _ENTITY,
    ),
    _u(
        "u17",
        "entity",
        "nbfc_aa",
        "What loan to value applies to gold loans given by an account aggregator?",
        r"gold loan|gold jewellery|gold ornament",
        _ENTITY,
    ),
    _u(
        "u18",
        "entity",
        "arc",
        "What rules apply to an asset reconstruction company issuing debit cards?",
        r"debit card",
        _ENTITY,
    ),
    _u(
        "u19",
        "entity",
        "nbfc_p2p",
        "What statutory liquidity ratio must a peer to peer lending platform maintain?",
        r"statutory liquidity ratio|\mSLR\M",
        _ENTITY,
    ),
    _u(
        "u20",
        "entity",
        "cic",
        "What rules govern a credit information company issuing credit cards?",
        r"credit card",
        _ENTITY,
    ),
    _u(
        "u21",
        "before",
        "cb",
        "What minimum statutory liquidity ratio must a commercial bank maintain?",
        r"statutory liquidity|\mSLR\M",
        _BEFORE,
        BEFORE,
    ),
    _u(
        "u22",
        "before",
        "sfb",
        "How often must a small finance bank conduct penetration testing of critical systems?",
        r"penetration test",
        _BEFORE,
        BEFORE,
    ),
    _u(
        "u23",
        "before",
        "ucb",
        "How long must an audit firm wait before an urban co-operative bank can reappoint it as statutory auditor?",
        r"reappoint",
        _BEFORE,
        BEFORE,
    ),
    _u(
        "u24",
        "before",
        "pb",
        "Within how long must a payments bank report a dividend declaration to RBI?",
        r"dividend",
        _BEFORE,
        BEFORE,
    ),
    _u(
        "u25",
        "before",
        "nbfc",
        "After how many days overdue does an NBFC classify a loan as non-performing?",
        r"non-performing|\mNPA",
        _BEFORE,
        BEFORE,
    ),
    _u(
        "u26",
        "before",
        "arc",
        "When must an asset reconstruction company classify an asset as a loss asset?",
        r"loss asset",
        _BEFORE,
        BEFORE,
    ),
    _u(
        "u27",
        "before",
        "cb",
        "Within what overdue period must a bank invoke a default loss guarantee?",
        r"\mDLG\M|default loss guarantee",
        _BEFORE,
        BEFORE,
    ),
    _u(
        "u28",
        "before",
        "rrb",
        "How should a regional rural bank value securities held for trading?",
        r"\mHFT\M|held for trading",
        _BEFORE,
        BEFORE,
    ),
    _u(
        "u29",
        "before",
        "nbfc_hfc",
        "What must a housing finance company record on fixed deposit receipts about nomination?",
        r"nominat",
        _BEFORE,
        BEFORE,
    ),
    _u(
        "u30",
        "before",
        "lab",
        "What must a local area bank do when a depositor claims an unclaimed deposit already transferred to the Fund?",
        r"unclaimed",
        _BEFORE,
        BEFORE,
    ),
    _u(
        "u31",
        "repealed",
        "aifi",
        "Can an all India financial institution remit a loan given to one of its directors without RBI's prior approval?",
        r"remitted without|remission",
        _DELETED,
        expect="superseded_only",
    ),
    _u(
        "u32",
        "repealed",
        "ucb",
        "Which director related loans are exempt from an urban co-operative bank's restrictions on loans to directors?",
        r"director.related loans",
        _DELETED,
        expect="superseded_only",
    ),
    _u(
        "u33",
        "repealed",
        "nbfc",
        "Do an NBFC's rules on loans to directors' relatives also apply to awarding contracts?",
        r"awarding of contracts",
        _DELETED,
        expect="superseded_only",
    ),
    _u(
        "u34",
        "repealed",
        "cb",
        "Must a public sector bank place copies of RBI's important directives and communications before its board?",
        r"important communications",
        _DELETED,
        expect="superseded_only",
    ),
    _u(
        "u35",
        "repealed",
        "cb",
        "May a bank fix housing loan instalments on a graduated basis?",
        r"graduated basis",
        _DELETED,
        expect="superseded_only",
    ),
    _u(
        "u36",
        "withdrawn",
        "cb",
        "Is circular DBOD.No.BP.BC.31/21.04.157/2008-09 on off-balance sheet exposures still in force?",
        "",
        _WITHDRAWN,
        expect="superseded_only",
    ),
    _u(
        "u37",
        "withdrawn",
        "cb",
        "Is circular DBOD.No.Dir.BC.103/C.218(H)-77 on advances against edible oilseeds still in force?",
        "",
        _WITHDRAWN,
        expect="superseded_only",
    ),
    _u(
        "u38",
        "withdrawn",
        "cb",
        "Is circular DBOD.No.GCS.BC.48/C.486-73 on the use of Hindi in banks still in force?",
        "",
        _WITHDRAWN,
        expect="superseded_only",
    ),
    _u(
        "u39",
        "withdrawn",
        "cb",
        "Is circular DBR.AML.No.14746/14.01.001/2014-15 on AML standards still in force?",
        "",
        _WITHDRAWN,
        expect="superseded_only",
    ),
    _u(
        "u40",
        "withdrawn",
        "cb",
        "Is circular DBOD.No.BP.1504/C.469(D)-89 on minimum capital and reserves still in force?",
        "",
        _WITHDRAWN,
        expect="superseded_only",
    ),
)

# Unanswerable questions found to be answerable after all, by the 23 November 2026 run: each
# was answered, or nearly, from a provision the retrieval check had missed. They stay listed
# (in ``sets/removed.jsonl``) so the removal is visible, and are scored in no set. A phrase
# check cannot see content that moved into a reworded paragraph; the remaining "repealed"
# entries were read by hand against the paragraphs the run retrieved for them.
REMOVED: dict[str, str] = {
    "u05": "answerable in substance: the general KYC rules for opening a company's account apply"
    " whatever its business; the composer drafted an answer from them, refused only by a"
    " numeric check false positive",
    "u32": "answerable: the director related loan exemptions moved into paragraph 7B of the UCB"
    " direction's third version, in force from 1 July 2026, worded so the phrase check missed"
    " them; the baseline answered from it",
    "u33": "answerable in part: paragraph 13J of the NBFC direction's third version, in force"
    " from 1 July 2026, extends the recusal rule to contracts; the baseline answered from it",
}

QUESTIONS: tuple[Question, ...] = (*ANSWERABLE, *AS_OF, *UNANSWERABLE)


def _splits() -> dict[tuple[str, str], str]:
    """Half the groups of each set to dev and half to test: groups are ranked by a hash and
    alternated, so the halves are balanced and never move when a question is added."""
    out: dict[tuple[str, str], str] = {}
    for name in ("answerable", "as_of", "unanswerable"):
        groups = sorted({(q.key, q.group or q.qid) for q in QUESTIONS if q.set == name})
        for i, (_, group) in enumerate(groups):
            out[(name, group)] = "dev" if i % 2 == 0 else "test"
    return out


_SPLITS = _splits()


# --------------------------------------------------------------------------- the checks


def _count(conn: Any, terms: str, *, entity: str | None, on: date | None) -> int:
    # Fixed fragments only; every value is a bound parameter.
    sql = (
        "SELECT count(DISTINCT c.para_id) FROM chunks c WHERE c.trusted AND c.body ~* %(t)s"  # noqa: S608
        + (" AND c.entity_codes && %(e)s::text[]" if entity else "")
        + (
            " AND c.valid_from <= %(d)s AND (c.valid_to IS NULL OR c.valid_to > %(d)s)"
            if on
            else ""
        )
    )
    return int(conn.execute(sql, {"t": terms, "e": [entity], "d": on}).fetchone()[0])


def evidence(conn: Any, q: Question, top: list[list[str]]) -> dict[str, Any]:
    """What the index does and does not contain for an unanswerable question."""
    ev: dict[str, Any] = {"top_retrieved": top}
    if q.source == "withdrawn":
        from extant.core.identity import find_references

        refs = find_references(q.text)
        canon = refs[0][1] if refs else None
        row = conn.execute(
            "SELECT withdrawn_by_doc_id, effective_on FROM withdrawals WHERE canonical_ref = %s",
            (canon,),
        ).fetchone()
        consolidated = conn.execute(
            "SELECT count(*) FROM concordance WHERE old_ref = %s", (canon,)
        ).fetchone()[0]
        ev |= {
            "canonical_ref": canon,
            "withdrawn_by": row[0] if row else None,
            "withdrawal_effective": row[1].isoformat() if row else None,
            "consolidated_into": int(consolidated),
        }
        return ev
    ev["in_scope_in_force"] = _count(conn, q.terms, entity=q.entity, on=q.as_of)
    if q.source == "entity":
        ev["other_entities_in_force"] = _count(conn, q.terms, entity=None, on=q.as_of)
    if q.source == "before":
        ev["in_scope_in_force_on_2026_11_20"] = _count(conn, q.terms, entity=q.entity, on=TODAY)
    if q.source == "repealed":
        ev["in_scope_any_version"] = _count(conn, q.terms, entity=q.entity, on=None)
    return ev


def validate(row: dict[str, Any]) -> None:
    """Raise unless an entry's evidence bears out its reason. Answerable entries need gold."""
    qid = row.get("qid", "?")
    if row["set"] in ("answerable", "as_of"):
        if not row.get("gold"):
            raise SetValidationError(f"{qid}: an answerable question needs a gold paragraph")
        return
    if not row.get("why") or not row.get("source"):
        raise SetValidationError(f"{qid}: an unanswerable question needs a recorded reason")
    ev = row.get("evidence")
    if not ev:
        raise SetValidationError(f"{qid}: no retrieval check recorded; an assertion is not enough")
    src = row["source"]
    if src == "withdrawn":
        ok = (
            bool(ev.get("withdrawn_by"))
            and ev.get("consolidated_into") == 0
            and (ev["withdrawal_effective"] <= row["as_of"])
        )
    else:
        ok = ev.get("in_scope_in_force") == 0
        if src == "entity":
            ok = ok and ev.get("other_entities_in_force", 0) > 0
        if src == "before":
            ok = ok and ev.get("in_scope_in_force_on_2026_11_20", 0) > 0
        if src == "repealed":
            ok = ok and ev.get("in_scope_any_version", 0) > 0
    if not ok:
        raise SetValidationError(f"{qid}: the retrieval check does not bear out '{src}': {ev}")


def to_row(q: Question) -> dict[str, Any]:
    return {
        "qid": q.qid,
        "set": q.set,
        "split": q.split,
        "text": q.text,
        "entity": q.entity,
        "as_of": q.as_of.isoformat(),
        "gold": list(q.gold),
        "group": q.group,
        "source": q.source,
        "why": q.why,
        "terms": q.terms,
        "expect": q.expect,
    }


def load(name: SetName) -> list[dict[str, Any]]:
    path = SETS_DIR / f"{name}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    for r in rows:
        validate(r)
    return rows


def check(schema: str) -> dict[str, list[dict[str, Any]]]:
    """Run every check against ``schema`` and return the rows, evidence included."""
    import psycopg
    from psycopg import sql

    from extant.index.settings import load_index_settings
    from extant.retrieval import RetrievalOptions, retrieve

    settings = load_index_settings()
    embedder = settings.embedder()
    out: dict[str, list[dict[str, Any]]] = {"answerable": [], "as_of": [], "unanswerable": []}
    with psycopg.connect(settings.writer_dsn.get_secret_value(), autocommit=True) as conn:
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        known = {
            r[0]
            for r in conn.execute(
                "SELECT para_id FROM paragraphs WHERE para_id = ANY(%s)",
                ([g for q in QUESTIONS for g in q.gold],),
            ).fetchall()
        }
        for q in QUESTIONS:
            row = to_row(q)
            missing = set(q.gold) - known
            if missing:
                raise SetValidationError(f"{q.qid}: gold paragraphs not in {schema}: {missing}")
            if q.set == "unanswerable":
                codes = (q.entity,) if q.entity else ()
                found = retrieve(
                    conn,
                    q.text,
                    codes,
                    q.as_of,
                    embedder=embedder,
                    options=RetrievalOptions(top_n=3, expand=False),
                )
                top = [[c.para_id, c.title[:80]] for c in found.candidates]
                row["evidence"] = evidence(conn, q, top)
                row["checked_on"] = schema
            out[q.set].append(row)
    return out


def write(rows: dict[str, list[dict[str, Any]]]) -> list[Path]:
    SETS_DIR.mkdir(parents=True, exist_ok=True)
    removed = [
        {**r, "removed": REMOVED[r["qid"]]} for r in rows["unanswerable"] if r["qid"] in REMOVED
    ]
    rows = {k: [r for r in v if r["qid"] not in REMOVED] for k, v in rows.items()}
    rows["removed"] = removed
    paths = []
    for name, items in rows.items():
        path = SETS_DIR / f"{name}.jsonl"
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in items),
            encoding="utf-8",
        )
        paths.append(path)
    return paths


def _build_schema() -> str:
    """The build to run against when none is named: EXTANT_BUILD_SCHEMA. Read only when a
    command needs the database, so a dry run needs no configuration."""
    from extant.core.settings import settings

    return settings.build_schema


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.answers.sets")
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--schema", help="default: EXTANT_BUILD_SCHEMA")
    args = parser.parse_args(argv)
    args.schema = args.schema or _build_schema()
    rows = check(args.schema)
    failed = []
    for r in rows["unanswerable"]:
        try:
            validate(r)
        except SetValidationError as exc:
            failed.append(str(exc))
    for name, items in rows.items():
        splits = {s: sum(r["split"] == s for r in items) for s in ("dev", "test")}
        print(f"{name}: {len(items)} questions {splits}")
    if failed:
        print("\n".join(failed), file=sys.stderr)
        return 1
    for path in write(rows):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
