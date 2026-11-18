"""Settings validate at import, so each case imports the module in a fresh interpreter with
a controlled environment, run from an empty directory so no .env is picked up."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROBE = (
    "from extant.core.settings import settings as s;"
    "print(s.daily_answer_cap, s.embedding_dim, s.embedding_quantisation, s.build_schema)"
)

VALID = {
    "EXTANT_WRITER_DSN": "postgresql://extant:writer-pw@localhost:54329/extant",
    "EXTANT_READER_DSN": "postgresql://extant_reader:reader-pw@localhost:54329/extant",
    "EXTANT_BUILD_SCHEMA": "idx_rbi_20261118_a3f91c4",
    "EXTANT_EMBEDDING_PROVIDER": "provider-x",
    "EXTANT_EMBEDDING_MODEL": "model-x",
    "EXTANT_LLM_PROVIDER": "provider-y",
    "EXTANT_ROUTER_MODEL": "router-y",
    "EXTANT_COMPOSER_MODEL": "composer-y",
}


def _import(env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("EXTANT_")}
    return subprocess.run(
        [sys.executable, "-c", PROBE],
        env={**base, **env},
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_complete_environment_imports(tmp_path: Path) -> None:
    result = _import(VALID, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["50", "256", "float16", "idx_rbi_20261118_a3f91c4"]


def test_values_from_env_example_are_accepted(tmp_path: Path) -> None:
    # Environment values are strings; the fixed ones must still parse.
    explicit = {
        "EXTANT_EMBEDDING_DIM": "256",
        "EXTANT_EMBEDDING_QUANTISATION": "float16",
        "EXTANT_DAILY_ANSWER_CAP": "50",
    }
    result = _import({**VALID, **explicit}, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[:3] == ["50", "256", "float16"]


def test_env_example_lists_every_setting(tmp_path: Path) -> None:
    probe = (
        "from extant.core.settings import ENV_PREFIX, Settings;"
        "print(' '.join(ENV_PREFIX + n.upper() for n in Settings.model_fields))"
    )
    base = {k: v for k, v in os.environ.items() if not k.startswith("EXTANT_")}
    result = subprocess.run(
        [sys.executable, "-c", probe],
        env={**base, **VALID},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    example = (Path(__file__).resolve().parents[2] / ".env.example").read_text()
    keys = {line.split("=", 1)[0] for line in example.splitlines() if "=" in line}
    assert set(result.stdout.split()) <= keys


def test_missing_settings_fail_at_import_with_a_clear_message(tmp_path: Path) -> None:
    env = {"EXTANT_WRITER_DSN": VALID["EXTANT_WRITER_DSN"]}
    result = _import(env, tmp_path)
    assert result.returncode != 0
    assert "SettingsError" in result.stderr
    for name in ("EXTANT_READER_DSN", "EXTANT_BUILD_SCHEMA", "EXTANT_EMBEDDING_MODEL"):
        assert f"{name}: is required but not set" in result.stderr
    assert "EXTANT_WRITER_DSN" not in result.stderr  # it was set and valid
    assert "writer-pw" not in result.stderr  # a DSN's password is never echoed


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("EXTANT_WRITER_DSN", "", "EXTANT_WRITER_DSN"),
        ("EXTANT_WRITER_DSN", "postgresql://localhost/extant", "user explicitly"),
        ("EXTANT_BUILD_SCHEMA", "public", "EXTANT_BUILD_SCHEMA"),
        ("EXTANT_EMBEDDING_MODEL", " ", "EXTANT_EMBEDDING_MODEL"),
        ("EXTANT_EMBEDDING_DIM", "512", "EXTANT_EMBEDDING_DIM"),
        ("EXTANT_EMBEDDING_QUANTISATION", "int8", "EXTANT_EMBEDDING_QUANTISATION"),
        ("EXTANT_DAILY_ANSWER_CAP", "0", "EXTANT_DAILY_ANSWER_CAP"),
        (
            "EXTANT_READER_DSN",
            "postgresql://extant:other-pw@localhost:54329/extant",
            "read only role",
        ),
    ],
)
def test_invalid_settings_fail_at_import(
    tmp_path: Path, key: str, value: str, message: str
) -> None:
    result = _import({**VALID, key: value}, tmp_path)
    assert result.returncode != 0
    assert message in result.stderr
    assert "-pw" not in result.stderr


def test_no_secret_in_repr(tmp_path: Path) -> None:
    probe = "from extant.core.settings import settings; print(repr(settings))"
    base = {k: v for k, v in os.environ.items() if not k.startswith("EXTANT_")}
    result = subprocess.run(
        [sys.executable, "-c", probe],
        env={**base, **VALID},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "writer-pw" not in result.stdout
    assert "reader-pw" not in result.stdout
