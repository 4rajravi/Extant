"""The Phase 2 commands: chunk, embed, assemble, verify. Called from ``__main__``."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import psycopg
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from extant.core.identity import make_schema_name
from extant.corpora.rbi.build import BASELINE, git_state, load_inputs, texts_to_embed
from extant.corpora.rbi.chunk import chunk_all, load_chunks
from extant.index.assemble import assemble
from extant.index.embed import PRICE_USD_PER_MTOK, EmbedRun, embed_missing, plan
from extant.index.settings import IndexSettings, IndexSettingsError, load_index_settings
from extant.index.verify import schema_bytes, verify

# The Phase 2 spec's working estimate, which the dry run is checked against.
SPEC_ESTIMATE_TOKENS = 36_000_000


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    sub.add_parser("chunk", help="step 5: chunk rows from the parsed paragraphs (offline)")
    embed = sub.add_parser("embed", help="step 6: vectors for chunks not yet in public.vec_cache")
    embed.add_argument("--dry-run", action="store_true", help="count, estimate, embed nothing")
    embed.add_argument("--sample", type=int, metavar="N", help="only the first N documents")
    assemble_ = sub.add_parser("assemble", help="steps 7 and 8: build, load and verify a schema")
    assemble_.add_argument("--schema", help="schema name (default idx_rbi_<today>_<commit>)")
    assemble_.add_argument(
        "--allow-dirty", action="store_true", help="build from uncommitted changes"
    )
    verify_ = sub.add_parser("verify", help="step 8 alone, on an existing build schema")
    verify_.add_argument("--schema", required=True)
    cands = sub.add_parser(
        "candidates", help="retrieve the smoke queries and write their candidates for marking"
    )
    cands.add_argument("--schema", required=True)
    pub = sub.add_parser(
        "publish", help="copy a verified build to EXTANT_PUBLISH_DSN (Neon) for serving and CI"
    )
    pub.add_argument("--schema", required=True)


COMMANDS = frozenset({"chunk", "embed", "assemble", "verify", "candidates", "publish"})
SMOKE = Path(__file__).resolve().parents[3] / "evals" / "smoke-rbi.toml"


def run(args: argparse.Namespace, data_dir: Path) -> int:
    try:
        return _run(args, data_dir)
    except (FileNotFoundError, IndexSettingsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace, data_dir: Path) -> int:
    if args.command == "chunk":
        print(chunk_all(data_dir))
        return 0

    settings = load_index_settings()
    identity = settings.identity
    dsn = settings.writer_dsn.get_secret_value()

    if args.command == "embed":
        print(chunk_all(data_dir))
        texts = texts_to_embed(load_chunks(data_dir), sample=args.sample)
        with psycopg.connect(dsn, autocommit=True) as conn:
            todo = plan(conn, texts, identity, eur_per_usd=settings.eur_per_usd)
            print(todo)
            if args.sample is None:
                ratio = SPEC_ESTIMATE_TOKENS / max(todo.estimated_tokens, 1)
                print(
                    f"spec estimate: {SPEC_ESTIMATE_TOKENS:,} tokens, {ratio:.1f}x this run's"
                    " estimate"
                )
            if args.dry_run or not todo.misses:
                return 0
            embedder = settings.embedder()

            def progress(r: EmbedRun) -> None:
                if r.batches % 10 == 0:
                    print(f"  {r.embedded:,} embedded, {r.tokens:,} tokens, {r.seconds:.0f}s")

            tpm = settings.embed_tokens_per_minute
            minutes = todo.estimated_tokens / tpm
            print(f"paced to {tpm:,} tokens a minute: about {minutes:.0f} minutes")
            done = embed_missing(
                conn, embedder, todo.misses, tokens_per_minute=tpm, progress=progress
            )
        usd = done.tokens / 1e6 * PRICE_USD_PER_MTOK.get(identity.model_id, 0.0)
        print(
            f"embedded {done.embedded:,} texts in {done.batches} requests, {done.tokens:,} tokens"
            f" as the provider counted them: ${usd:.4f}, about {usd * settings.eur_per_usd:.4f}"
            f" EUR, {done.seconds:.0f}s"
        )
        return 0

    if args.command == "candidates":
        return _candidates(dsn, args.schema, settings)

    if args.command == "publish":
        return _publish(dsn, args.schema)

    with psycopg.connect(dsn, autocommit=True) as conn:
        if args.command == "verify":
            report = verify(conn, args.schema, baseline=BASELINE)
            print(f"{args.schema}: passes against {report.compared_against}: {report.counts}")
            return 0

        commit, dirty = git_state()
        if dirty and not args.allow_dirty:
            print(
                "error: the working tree has uncommitted changes, so the manifest's commit"
                " would not reproduce this build; commit first or pass --allow-dirty",
                file=sys.stderr,
            )
            return 2
        print(chunk_all(data_dir))
        schema = args.schema or make_schema_name("rbi", date.today(), commit)
        inputs = load_inputs(data_dir, git_commit=commit, git_dirty=dirty)
        result = assemble(conn, schema, inputs, identity, baseline=BASELINE)
        size = schema_bytes(conn, schema)
    against = result.verify.compared_against if result.verify else "nothing"
    print(f"built {schema} in {result.seconds:.0f}s, verified against {against}")
    print(f"counts: {result.counts}")
    print(f"size on disk: {size / 1e6:.1f} MB")
    return 0


class PublishSettings(BaseSettings):
    """The publish target. Both values live in .env only; the reader DSN is also the CI
    gate's repository secret."""

    model_config = SettingsConfigDict(env_prefix="EXTANT_", env_file=".env", extra="ignore")
    publish_dsn: SecretStr
    publish_reader_dsn: SecretStr


def _publish(dsn: str, schema: str) -> int:
    from pydantic import ValidationError

    from extant.index.publish import publish

    try:
        target = PublishSettings()
    except ValidationError:
        print(
            "error: set EXTANT_PUBLISH_DSN (an owner connection to the target) and"
            " EXTANT_PUBLISH_READER_DSN (extant_reader on the target) in .env",
            file=sys.stderr,
        )
        return 2
    with (
        psycopg.connect(dsn, autocommit=True) as source,
        psycopg.connect(target.publish_dsn.get_secret_value(), autocommit=True) as dest,
    ):
        verify(source, schema, baseline=BASELINE)  # only a build that verifies is published
        result = publish(
            source, dest, schema, reader_dsn=target.publish_reader_dsn.get_secret_value()
        )
    print(f"published {result.schema}: {result.counts}")
    return 0


def _candidates(dsn: str, schema: str, settings: IndexSettings) -> int:
    """The top ten for each smoke query, written beside the suite for a person to mark."""
    import tomllib

    from psycopg import sql

    from extant.retrieval import retrieve

    queries = tomllib.loads(SMOKE.read_text(encoding="utf-8"))["query"]
    embedder = settings.embedder()
    out = [f"# Smoke query candidates, {schema}", ""]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        for q in queries:
            result = retrieve(conn, q["text"], q["entity_codes"], q["as_of"], embedder=embedder)
            out += [
                f"## {q['id']} ({q['type']}): {q['text']}",
                "",
                f"Entity codes {q['entity_codes']}, as of {q['as_of']}, {result.path} path.",
                "",
            ]
            for c in result.candidates:
                text = " ".join(c.body.split())[:400]
                out += [f"{c.rank}. `{c.para_id}` {c.title}", f"   > {text}", ""]
            for m in result.references:
                out.append(
                    f"Reference {m.canonical_ref}: withdrawn {m.withdrawals},"
                    f" consolidated into {m.consolidated_into}"
                )
            out.append("")
    path = SMOKE.with_name(f"smoke-rbi-candidates-{schema}.md")
    path.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {path}")
    return 0
