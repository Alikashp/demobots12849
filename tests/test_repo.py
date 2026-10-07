"""Критерий 1.9: секретов и базы нет в репозитории (Д7, А2, А8)."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOKEN_RE = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")


def tracked_files() -> list[Path]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("нет git-репозитория")
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ROOT / line for line in out.splitlines() if line]


def test_no_database_or_env_files_in_repo():
    names = [p.name for p in tracked_files()]
    assert not [n for n in names if n.endswith((".db", ".sqlite3", "-wal", "-shm"))]
    assert ".env" not in names


def test_no_bot_token_in_repo():
    for path in tracked_files():
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert not TOKEN_RE.search(text), path


def test_secrets_only_from_environment():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in env_example.splitlines():
        if line.startswith(("BOT_TOKEN=", "ADMIN_CHAT_ID=")):
            assert line.endswith("="), "в примере окружения не должно быть значений"
