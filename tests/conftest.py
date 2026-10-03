"""Shared fixtures. Every test runs against a throwaway data folder and settings file,
so the suite can never read or modify the user's real data."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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
