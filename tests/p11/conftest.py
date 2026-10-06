"""P11 (dev/dispatch.py) runs the P2 stand-in CLIs as the coder and the reviewer, against a
throwaway git repository and a throwaway app data folder."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.accounts import Accounts
from app.config import Settings
from tests.p2.conftest import fake_cmds, memory_keyring  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dev"))
import dispatch  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True,
                          encoding="utf-8").stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.name", "Test")
    git(r, "config", "user.email", "test@example.com")
    git(r, "config", "core.autocrlf", "false")
    (r / ".gitignore").write_text("build/\n", encoding="utf-8")
    (r / "CLAUDE.md").write_text("# test repo\n", encoding="utf-8")
    (r / "app.py").write_text("print('hello')\n", encoding="utf-8")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "start")
    return r


@pytest.fixture
def env(tmp_path, fake_cmds, monkeypatch):
    for k in ("FAKE_MODE", "FAKE_MODE_CLAUDE", "FAKE_MODE_CODEX", "FAKE_UTIL", "FAKE_WRITE",
              "FAKE_REPLY", "FAKE_CODEX_REPLY", "FAKE_CODEX_PCT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "calls.txt"))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "prompts.jsonl"))
    monkeypatch.setenv("FAKE_ARGS", str(tmp_path / "args.jsonl"))
    settings = Settings(data_dir=tmp_path / "data", claude_cmd=str(fake_cmds / "claude.cmd"),
                        codex_cmd=str(fake_cmds / "codex.cmd"))
    settings.ensure_dirs()
    return settings


def account(settings, provider, signed_in=True, label=""):
    acc = Accounts(settings.data_dir).add(provider, label=label)
    if signed_in:
        (Path(acc["profile_dir"]) / "logged_in").write_text(f"{provider}@example.com")
    return acc


def codex_used(acc, pct):
    """What the real Codex leaves in its session file: exact limits."""
    d = Path(acc["profile_dir"]) / "sessions" / "2026" / "10" / "06"
    d.mkdir(parents=True, exist_ok=True)
    (d / "rollout-2026-10-06T10-00-00-x.jsonl").write_text(json.dumps({"payload": {"rate_limits": {
        "primary": {"used_percent": pct, "window_minutes": 300, "resets_at": 4102444800},
        "secondary": {"used_percent": 5, "window_minutes": 10080, "resets_at": 4102444800}}}}) + "\n",
        encoding="utf-8")


def prompts(tmp_path) -> list[dict]:
    p = tmp_path / "prompts.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def argvs(tmp_path) -> list[list[str]]:
    p = tmp_path / "args.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
