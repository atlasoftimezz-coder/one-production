import json

import pytest
from pydantic import ValidationError

from app.config import DATA_SUBDIRS, Settings, load_settings


def test_default_data_dir_is_under_localappdata(isolated):
    s = load_settings()
    assert s.data_dir == isolated / "local" / "OneProduction"
    assert s.host == "127.0.0.1"


def test_settings_file_overrides_default(isolated):
    (isolated / "settings.json").write_text(
        json.dumps({"data_dir": str(isolated / "fromfile"), "port": 9001}), encoding="utf-8")
    s = load_settings()
    assert s.data_dir == isolated / "fromfile"
    assert s.port == 9001


def test_env_overrides_settings_file(isolated, monkeypatch):
    (isolated / "settings.json").write_text(
        json.dumps({"data_dir": str(isolated / "fromfile")}), encoding="utf-8")
    monkeypatch.setenv("ONEPROD_DATA_DIR", str(isolated / "fromenv"))
    monkeypatch.setenv("ONEPROD_OPEN_BROWSER", "false")
    s = load_settings()
    assert s.data_dir == isolated / "fromenv"
    assert s.open_browser is False


def test_ensure_dirs_creates_all_subfolders(isolated):
    s = Settings(data_dir=isolated / "data")
    s.ensure_dirs()
    for name in DATA_SUBDIRS:
        assert (isolated / "data" / name).is_dir()


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "localhost", "::"])
def test_non_loopback_host_is_refused(isolated, host):
    with pytest.raises(ValidationError):
        Settings(data_dir=isolated / "data", host=host)
