"""Loading and rendering the versioned SQL files in ``migrations/``.

    migrations/public/NNNN_*.sql   idempotent, applied in order by ``bootstrap``
    migrations/index/NNNN_*.sql    applied in order to each new build schema by ``create``
    migrations/index/grants.sql    applied last by ``create``, after every numbered file

Placeholders such as ``{schema}`` are filled with ``psycopg.sql`` composables, which quote
identifiers and literals correctly. Names are never pasted in with an f-string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from psycopg import sql

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

_NUMBERED = re.compile(r"(?P<version>[0-9]{4})_[a-z0-9_]+\.sql")
_UNNUMBERED_INDEX_FILES = frozenset({"grants.sql"})


@dataclass(frozen=True, slots=True)
class Migration:
    path: Path

    @property
    def name(self) -> str:
        return self.path.stem

    def render(self, **placeholders: sql.Composable) -> sql.Composed:
        # The file is trusted source from this repository; only the placeholders vary, and
        # those arrive as psycopg composables.
        return sql.SQL(self.path.read_text(encoding="utf-8")).format(**placeholders)


def _numbered(directory: Path, allowed_extra: frozenset[str] = frozenset()) -> list[Migration]:
    if not directory.is_dir():
        raise FileNotFoundError(f"migrations directory not found: {directory}")
    found: list[Migration] = []
    for path in sorted(directory.iterdir()):
        if path.name in allowed_extra:
            continue
        if _NUMBERED.fullmatch(path.name) is None:
            raise ValueError(f"unexpected file in {directory}: {path.name}")
        found.append(Migration(path))
    versions = [m.name[:4] for m in found]
    if len(set(versions)) != len(versions):
        raise ValueError(f"duplicate migration version in {directory}: {versions}")
    return found


def public_migrations() -> list[Migration]:
    return _numbered(MIGRATIONS_DIR / "public")


def index_migrations() -> list[Migration]:
    return _numbered(MIGRATIONS_DIR / "index", _UNNUMBERED_INDEX_FILES)


def index_grants() -> Migration:
    return Migration(MIGRATIONS_DIR / "index" / "grants.sql")
