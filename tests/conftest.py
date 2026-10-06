"""Shared fixtures. Every test runs against a throwaway data folder and settings file,
so the suite can never read or modify the user's real data."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Never the owner's real Credential Manager - not here, and not in any app server a test starts
# as a subprocess (they inherit this). Tests that need a key store use their own memory one.
os.environ["PYTHON_KEYRING_BACKEND"] = "app.security.memkeyring.MemoryKeyring"
import keyring  # noqa: E402
from app.security.memkeyring import MemoryKeyring  # noqa: E402
keyring.set_keyring(MemoryKeyring())

ENV_KEYS = ("ONEPROD_DATA_DIR", "ONEPROD_PORT", "ONEPROD_OPEN_BROWSER",
            "ONEPROD_LOG_LEVEL", "ONEPROD_SETTINGS_FILE")


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ONEPROD_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    return tmp_path
