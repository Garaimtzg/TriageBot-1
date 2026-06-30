"""Guardrail test: the shipped code must not contain hardcoded secrets.

Workshop rule (CLAUDE.md): the API key is *never* hardcoded; it is read from the
environment variable whose **name** lives in ``config.yaml``
(``classifier.api_key_env``). This test scans the application source and the
config file and fails if anyone commits an actual key/secret literal, so a leaked
credential can't slip in unnoticed.

It deliberately does NOT scan ``tests/`` (fixtures legitimately use dummy values
like ``"test-key"``) nor ``.env`` (which is gitignored and never committed).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Files we hold to the "no secrets" standard: the app package and the config.
SCANNED_FILES = sorted((PROJECT_ROOT / "app").rglob("*.py")) + [
    PROJECT_ROOT / "config.yaml"
]

# Provider key formats (OpenRouter / OpenAI / Anthropic ...). If a literal that
# looks like one of these appears anywhere, it's almost certainly a real secret.
KEY_PREFIX_PATTERN = re.compile(r"\b(sk-or-v1-|sk-proj-|sk-ant-|sk-[A-Za-z0-9]{16})")

# A secret-ish variable being assigned a non-empty string *literal* in Python,
# e.g. ``api_key = "abc123"``. This does NOT match ``api_key = os.getenv(...)``
# nor ``api_key=api_key`` (those assign an expression, not a quoted literal).
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"""(?ix)            # case-insensitive, verbose
    \b(api[_-]?key|secret|token|passwd|password)   # secret-ish name
    \s*=\s*              # assignment
    ["'][^"']+["']       # to a non-empty string literal
    """
)


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _offending_lines(path: Path) -> list[str]:
    findings: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if KEY_PREFIX_PATTERN.search(line) or SECRET_ASSIGNMENT_PATTERN.search(line):
            findings.append(f"{_display_path(path)}:{lineno}: {line.strip()}")
    return findings


@pytest.mark.parametrize("path", SCANNED_FILES, ids=lambda p: str(p.name))
def test_source_file_has_no_hardcoded_secret(path: Path):
    offenders = _offending_lines(path)
    assert not offenders, (
        "Hardcoded secret/API key detected. Read it from an environment variable "
        "instead (see classifier.api_key_env in config.yaml):\n  "
        + "\n  ".join(offenders)
    )


def test_scanner_actually_flags_a_planted_secret(tmp_path):
    """Sanity check: the scanner catches a real-looking key, so a green suite means something."""
    planted = tmp_path / "leak.py"
    planted.write_text('api_key = "sk-or-v1-deadbeefdeadbeefdeadbeef"\n', encoding="utf-8")

    assert _offending_lines(planted), "scanner failed to detect an obvious hardcoded key"
