"""The eval sets: built from the Phase 1 label files, never from the serving path.

Each set is a JSONL file of ``EvalQuery`` rows in ``evals/sets/rbi/``, named for the parser
version that produced its labels (``temporal.p2.jsonl``), with ``MANIFEST.json`` recording
every version, the seed, the holdout cutoff and the sizes. Loading refuses a set whose parser
version no longer matches the label files, since its gold would describe paragraphs the
current parser does not produce.

The sets, and what each one cannot tell you:

- ``concordance``: traceback, an old circular reference to the direction that consolidated
  it, from the repeal annexes. Phase 1 found 4 such pairs in the whole corpus, all document
  level, so the set has 4 queries and no paragraph level gold. It measures nothing a reader
  should act on; it is kept so the table shows the gap instead of hiding it.
- ``withdrawal``: supersession detection, from the published withdrawal lists. A seeded
  sample of 1,000 of the 10,019 withdrawn circulars. Near trivial once the list is ingested,
  because the fast path looks the reference up in that same list: a regression canary, not a
  benchmark. The 154 rows with no reference ("Mail Box Clarification") can only be asked by
  title, and the hybrid path never consults the withdrawal list, so those score 0.
- ``temporal``: as-of correctness, from amended directions. For each pair of consecutive
  versions with text, one paragraph that changed between them gives two queries with the same
  text: one on the last day of the old version (gold: the old paragraph) and one on the first
  day of the new version (gold: the new paragraph). The query text is the old paragraph's own
  first sentence, so lexical overlap is maximal: the set tests whether the right version comes
  back on the date, not whether a paraphrased question finds it.
- ``faq``: not built. Phase 1 mined no FAQ labels.

Splits are by group (a direction, or a circular), so near-duplicate queries never straddle dev
and test. The holdout flag marks queries whose gold material was published after the
generation model's training cutoff.

Gold entity scope and validity windows come from the chunk files (``derived/chunks``): the
index-plane derivation of the same rule the index uses. So a set built here cannot catch a
wrong validity rule, only a filter that disobeys the rule.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

REPO = Path(__file__).resolve().parents[1]
SETS_DIR = REPO / "evals" / "sets" / "rbi"
LABELS_DIR = REPO / "evals" / "labels" / "rbi"
SAMPLE_FILE = REPO / "evals" / "samples" / "rbi-sample.json"
DATA_DIR = REPO / "data" / "rbi"

# 2: a provision deleted by an unbracketed marker ("61. *****", "*****]") no longer becomes a
# new-side query; builder 1 caught only the bracketed form "[***]".
BUILDER_VERSION = "2"
SEED = 20261120
# The as-of date for questions about the present, fixed so a rebuilt set is the same set.
EVAL_AS_OF = date(2026, 11, 20)
# The memorisation holdout: material published after the generation model's training cutoff.
# Recorded per set build, because it changes when the model changes.
HOLDOUT_MODEL = "claude-opus-5-5"
HOLDOUT_CUTOFF = date(2026, 6, 30)
WITHDRAWAL_SAMPLE = 1_000
SET_NAMES = ("concordance", "withdrawal", "temporal", "faq")
NOT_BUILT = {"faq": "Phase 1 mined no FAQ labels; the RBI FAQ recon decides whether to mine them"}

Split = Literal["dev", "test"]


class StaleSetError(RuntimeError):
    """A set file no longer matches the label files or the code that builds it."""


class EvalQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    set: str
    query_id: str
    split: Split
    group: str  # the split unit
    text: str
    entity_codes: tuple[str, ...]
    as_of: date
    level: Literal["paragraph", "document"]  # the finest level the gold labels reach
    gold_para_ids: tuple[str, ...]  # empty where the labels are document level
    gold_doc_ids: tuple[str, ...]
    published_on: date  # when the gold material was published
    holdout: bool  # published after HOLDOUT_CUTOFF
    form: str  # how the query was phrased, e.g. "reference" or "title"
    source: dict[str, Any]  # the label rows it came from, for the audit


@dataclass(frozen=True)
class EvalSet:
    name: str
    queries: tuple[EvalQuery, ...]

    def split(self, split: Split) -> list[EvalQuery]:
        return [q for q in self.queries if q.split == split]


# --------------------------------------------------------------------------- helpers


def _jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _hash(*parts: object) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def assign_split(group: str, seed: int = SEED) -> Split:
    """Half the groups to dev and half to test, by a hash of the group: deterministic,
    independent of set size and of the order rows were read in."""
    return "dev" if int(_hash(seed, group)[:8], 16) % 2 == 0 else "test"


def label_versions() -> dict[str, str]:
    manifest = json.loads((LABELS_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    return {k: manifest[k] for k in ("html_extractor", "parser", "miner")}


# Paragraph numbering at the start of a body: "7.", "(a)", "iii)", "2.1", in any combination.
_ENUMERATOR = re.compile(
    r"^(?:\s*(?:\(?[0-9]{1,3}(?:\.[0-9]{1,3})*[.)]|\(?[a-zA-Z]{1,4}\)|\([ivxlc]{1,6}\)))+\s+"
)
_SENTENCE_END = re.compile(r"(?<=[a-z)\]][.;])\s+(?=[A-Z(])")


def first_sentence(text: str, max_words: int = 30) -> str:
    """The paragraph's first sentence without its numbering, cut to ``max_words``."""
    flat = _ENUMERATOR.sub("", " ".join(text.split()), count=1)
    head = _SENTENCE_END.split(flat, maxsplit=1)[0]
    return " ".join(head.split()[:max_words])


# --------------------------------------------------------------------------- sources


@dataclass(frozen=True)
class Sources:
    documents: dict[str, dict[str, Any]]
    paragraphs: dict[str, dict[str, str]]  # doc_id -> anchor -> body
    codes: dict[str, tuple[str, ...]]  # para_id -> entity codes
    windows: dict[str, tuple[date, date | None]]  # doc_id -> [valid_from, valid_to)
    versions: list[dict[str, Any]]
    all_codes: tuple[str, ...]


def load_sources(data_dir: Path = DATA_DIR) -> Sources:
    from extant.corpora.rbi.chunk import load_chunks

    derived = data_dir / "derived"
    documents = {d["doc_id"]: d for d in _jsonl(derived / "parsed" / "documents.jsonl")}
    paragraphs: dict[str, dict[str, str]] = defaultdict(dict)
    for p in _jsonl(derived / "parsed" / "paragraphs.jsonl"):
        doc_id, anchor = p["para_id"].split("#", 1)
        paragraphs[doc_id][anchor] = p["body"]
    codes: dict[str, set[str]] = defaultdict(set)
    windows: dict[str, tuple[date, date | None]] = {}
    for c in load_chunks(data_dir):
        codes[c.para_id].update(c.entity_codes)
        windows[c.doc_id] = (c.valid_from, c.valid_to)
    all_codes = tuple(sorted(e["code"] for e in _jsonl(derived / "entity_types.jsonl")))
    return Sources(
        documents=documents,
        paragraphs=dict(paragraphs),
        codes={k: tuple(sorted(v)) for k, v in codes.items()},
        windows=windows,
        versions=_jsonl(derived / "versions.jsonl"),
        all_codes=all_codes,
    )


def _doc_codes(src: Sources, doc_id: str) -> tuple[str, ...]:
    prefix = doc_id + "#"
    found = {c for pid, cs in src.codes.items() if pid.startswith(prefix) for c in cs}
    return tuple(sorted(found))


# --------------------------------------------------------------------------- builders


def build_concordance(src: Sources) -> list[EvalQuery]:
    out = []
    for row in _jsonl(LABELS_DIR / "supersession.jsonl"):
        new_doc = row["new_doc_id"]
        published = date.fromisoformat(src.documents[new_doc]["issued_on"])
        out.append(
            EvalQuery(
                set="concordance",
                query_id="conc-" + _hash(row["old_ref"])[:10],
                split=assign_split(new_doc),
                group=new_doc,
                text=f"What replaced circular {row['old_ref']}?",
                entity_codes=_doc_codes(src, new_doc),
                as_of=EVAL_AS_OF,
                level="paragraph" if row["new_para_id"] else "document",
                gold_para_ids=(row["new_para_id"],) if row["new_para_id"] else (),
                gold_doc_ids=(new_doc,),
                published_on=published,
                holdout=published > HOLDOUT_CUTOFF,
                form="reference",
                source=row,
            )
        )
    return out


def build_withdrawal(src: Sources, sample: int = WITHDRAWAL_SAMPLE) -> list[EvalQuery]:
    rows = [
        r
        for name in ("withdrawals.jsonl", "withdrawals_supervision.jsonl")
        for r in _jsonl(LABELS_DIR / name)
    ]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        # A circular with no reference is its own group, keyed by its withdrawal record.
        groups[r["canonical_ref"] or "key:" + r["withdrawal_key"]].append(r)
    chosen = random.Random(SEED).sample(sorted(groups), min(sample, len(groups)))
    out = []
    for group in sorted(chosen):
        members = sorted(groups[group], key=lambda r: r["withdrawal_key"])
        first = members[0]
        by_reference = first["canonical_ref"] is not None
        text = (
            f"Is circular {first['circular_ref']} still in force?"
            if by_reference
            else f'Is the RBI circular "{first["title"]}" dated {first["circular_date"]}'
            " still in force?"
        )
        published = max(date.fromisoformat(r["announced_on"]) for r in members)
        out.append(
            EvalQuery(
                set="withdrawal",
                query_id="wd-" + _hash(group)[:10],
                split=assign_split(group),
                group=group,
                text=text,
                entity_codes=src.all_codes,
                as_of=EVAL_AS_OF,
                level="document",
                gold_para_ids=(),
                gold_doc_ids=tuple(sorted({r["withdrawn_by_doc_id"] for r in members})),
                published_on=published,
                holdout=published > HOLDOUT_CUTOFF,
                form="reference" if by_reference else "title",
                source={"rows": members},
            )
        )
    return out


# A provision deleted by an amendment keeps its number with a marker for a body.
# Any body that is only punctuation (stars, brackets) with at most one of these words:
# "*****", "[***]", "*****]", "[Deleted].", "Omitted".
# The paragraph number alone, with or without a space after it ("125.[Deleted]").
_NUMBER = re.compile(r"^\s*\(?[0-9]{1,3}(?:\.[0-9]{1,3})*[.)]?\s*")
_DELETED = re.compile(r"^[\W_]*(?:(?:deleted|omitted|withdrawn)[\W_]*)?$", re.IGNORECASE)


def _excerpt(text: str, limit: int = 300) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "..."


def build_temporal(src: Sources) -> list[EvalQuery]:
    chains: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for v in src.versions:
        if v["doc_id"] in src.paragraphs and v["doc_id"] in src.windows:
            chains[v["direction_id"]].append(v)
    # Which directions each first sentence occurs in. A query is only useful if its text
    # points at one direction: boilerplate ("These Directions shall come into effect...")
    # opens a paragraph in nearly every direction and would make the gold one arbitrary.
    direction_of = {v["doc_id"]: v["direction_id"] for v in src.versions}
    seen_in: dict[str, set[int]] = defaultdict(set)
    for doc_id, paras in src.paragraphs.items():
        for body in paras.values():
            seen_in[first_sentence(body).lower()].add(direction_of.get(doc_id, -1))
    out = []
    for direction, chain in sorted(chains.items()):
        chain.sort(key=lambda v: v["version"])
        for old, new in itertools.pairwise(chain):
            a, b = old["doc_id"], new["doc_id"]
            pa, pb = src.paragraphs[a], src.paragraphs[b]
            changed = sorted(
                s
                for s in set(pa) & set(pb)
                if " ".join(pa[s].split()) != " ".join(pb[s].split())
                and len(first_sentence(pa[s]).split()) >= 6
                and seen_in[first_sentence(pa[s]).lower()] == {direction}
            )
            if not changed:
                continue
            anchor = random.Random(_hash(SEED, a, b)).choice(changed)
            text = first_sentence(pa[anchor])
            new_from = src.windows[b][0]
            last_old_day = new_from - timedelta(days=1)
            if src.windows[a][0] > last_old_day:
                continue  # the old version was never in force for a whole day
            sides = [("old", a, last_old_day)]
            if not _DELETED.match(_NUMBER.sub("", " ".join(pb[anchor].split()), count=1)):
                sides.append(("new", b, new_from))  # a deleted provision has no text to find
            for side, doc, as_of in sides:
                para = f"{doc}#{anchor}"
                published = src.windows[doc][0]
                out.append(
                    EvalQuery(
                        set="temporal",
                        query_id=f"temp-{_hash(a, b, anchor)[:10]}-{side}",
                        split=assign_split(f"direction:{direction}"),
                        group=f"direction:{direction}",
                        text=text,
                        entity_codes=src.codes[para],
                        as_of=as_of,
                        level="paragraph",
                        gold_para_ids=(para,),
                        gold_doc_ids=(doc,),
                        published_on=published,
                        holdout=published > HOLDOUT_CUTOFF,
                        form=f"first sentence, {side} version",
                        source={
                            "direction_id": direction,
                            "old_doc_id": a,
                            "new_doc_id": b,
                            "anchor": anchor,
                            "old_excerpt": _excerpt(pa[anchor]),
                            "new_excerpt": _excerpt(pb[anchor]),
                            "old_window": [str(x) if x else None for x in src.windows[a]],
                            "new_window": [str(x) if x else None for x in src.windows[b]],
                        },
                    )
                )
    return out


BUILDERS = {
    "concordance": build_concordance,
    "withdrawal": build_withdrawal,
    "temporal": build_temporal,
}


# --------------------------------------------------------------------------- files


def set_file(name: str, parser: str) -> Path:
    return SETS_DIR / f"{name}.{parser}.jsonl"


def _dumps(q: EvalQuery) -> str:
    return json.dumps(q.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


def write_sets(sets: dict[str, list[EvalQuery]]) -> dict[str, Any]:
    versions = label_versions()
    SETS_DIR.mkdir(parents=True, exist_ok=True)
    entries: dict[str, Any] = {}
    for name, queries in sorted(sets.items()):
        queries = sorted(queries, key=lambda q: q.query_id)
        path = set_file(name, versions["parser"])
        path.write_text("".join(_dumps(q) + "\n" for q in queries), encoding="utf-8")
        entries[name] = {
            "file": path.name,
            "queries": len(queries),
            "dev": sum(q.split == "dev" for q in queries),
            "test": sum(q.split == "test" for q in queries),
            "holdout": sum(q.holdout for q in queries),
            "level": sorted({q.level for q in queries}),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    manifest = {
        "builder": BUILDER_VERSION,
        **versions,
        "seed": SEED,
        "eval_as_of": EVAL_AS_OF.isoformat(),
        "holdout": {"model": HOLDOUT_MODEL, "cutoff": HOLDOUT_CUTOFF.isoformat()},
        "sets": entries,
        "not_built": NOT_BUILT,
    }
    (SETS_DIR / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def read_manifest() -> dict[str, Any]:
    path = SETS_DIR / "MANIFEST.json"
    if not path.exists():
        raise StaleSetError(f"{path} does not exist: run `uv run python -m evals.sets build`")
    manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return manifest


def load_sets(names: Iterable[str] | None = None) -> dict[str, EvalSet]:
    """The built sets, checked against the label files' versions and their own hashes."""
    manifest = read_manifest()
    versions = label_versions()
    stale = {k: (manifest.get(k), v) for k, v in versions.items() if manifest.get(k) != v}
    if stale:
        raise StaleSetError(f"eval sets were built from other label versions: {stale}; rebuild")
    if manifest["builder"] != BUILDER_VERSION:
        raise StaleSetError(
            f"eval sets are builder {manifest['builder']}, code is {BUILDER_VERSION}"
        )
    wanted = set(names) if names is not None else set(manifest["sets"])
    unknown = wanted - set(manifest["sets"])
    if unknown:
        raise StaleSetError(f"no built set named {sorted(unknown)}")
    out = {}
    for name in sorted(wanted):
        entry = manifest["sets"][name]
        path = SETS_DIR / entry["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise StaleSetError(f"{path.name} changed since it was built; rebuild, never hand edit")
        queries = tuple(EvalQuery.model_validate(r) for r in _jsonl(path))
        out[name] = EvalSet(name, queries)
    return out


# --------------------------------------------------------------------------- the reduced corpus


def build_sample(
    sets: dict[str, EvalSet], *, queries: int = 120, distractors: int = 120, seed: int = SEED
) -> dict[str, Any]:
    """The reduced corpus for the arms that re-embed (5 and 6) and the reranker (4).

    Sampled by document, and built so every gold paragraph of the evaluated queries is in it:
    the evaluated queries are a seeded draw of temporal dev queries, taken in old/new pairs,
    and the corpus is their gold documents plus a seeded draw of other documents as
    distractors. A random corpus sample would drop gold paragraphs and score its own
    sampling instead of the embeddings."""
    from extant.corpora.rbi.chunk import load_chunks

    temporal = sets["temporal"].split("dev")
    pairs: dict[str, list[EvalQuery]] = defaultdict(list)
    for q in temporal:
        pairs[q.query_id.rsplit("-", 1)[0]].append(q)
    rng = random.Random(seed)
    chosen_pairs = rng.sample(sorted(pairs), min(queries // 2, len(pairs)))
    chosen = sorted(q.query_id for p in chosen_pairs for q in pairs[p])
    by_id = {q.query_id: q for q in temporal}
    gold_docs = sorted({d for qid in chosen for d in by_id[qid].gold_doc_ids})
    corpus = sorted({c.doc_id for c in load_chunks(DATA_DIR)})
    others = sorted(set(corpus) - set(gold_docs))
    extra = sorted(rng.sample(others, min(distractors, len(others))))
    sample = {
        "seed": seed,
        "set": "temporal",
        "split": "dev",
        "queries": chosen,
        "gold_documents": gold_docs,
        "distractor_documents": extra,
        "documents": len(gold_docs) + len(extra),
        "corpus_documents": len(corpus),
    }
    SAMPLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_FILE.write_text(json.dumps(sample, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return sample


def read_sample() -> dict[str, Any]:
    if not SAMPLE_FILE.exists():
        raise StaleSetError(f"{SAMPLE_FILE} does not exist: run `python -m evals.sets sample`")
    sample: dict[str, Any] = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    return sample


def sample_queries() -> list[EvalQuery]:
    sample = read_sample()
    by_id = {q.query_id: q for q in load_sets([sample["set"]])[sample["set"]].queries}
    # A query the current builder no longer makes (builder 2 dropped new-side queries whose
    # gold is a deletion marker) is skipped; the sample's documents stay as drawn, so its
    # vectors stay valid and nothing is re-embedded.
    return [by_id[qid] for qid in sample["queries"] if qid in by_id]


# --------------------------------------------------------------------------- command line


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.sets")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="build every set from the label files")
    sample = sub.add_parser("sample", help="draw the reduced corpus for arms 4 to 6")
    sample.add_argument("--queries", type=int, default=120)
    sample.add_argument("--distractors", type=int, default=120)
    args = parser.parse_args(argv)
    if args.command == "build":
        src = load_sources()
        manifest = write_sets({name: build(src) for name, build in BUILDERS.items()})
        for name, entry in manifest["sets"].items():
            print(
                f"{name}: {entry['queries']} queries ({entry['dev']} dev, {entry['test']} test,"
                f" {entry['holdout']} holdout), {entry['file']}"
            )
        for name, why in NOT_BUILT.items():
            print(f"{name}: not built: {why}")
        return 0
    drawn = build_sample(load_sets(), queries=args.queries, distractors=args.distractors)
    print(
        f"sample: {len(drawn['queries'])} queries, {drawn['documents']} of"
        f" {drawn['corpus_documents']} documents ({len(drawn['gold_documents'])} gold,"
        f" {len(drawn['distractor_documents'])} distractors), {SAMPLE_FILE.name}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
