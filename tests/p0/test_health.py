from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.version import __version__


def _client(tmp_path):
    # P1 added a Host check: requests must come as 127.0.0.1, as a real browser's do.
    return TestClient(create_app(Settings(data_dir=tmp_path / "data")), base_url="http://127.0.0.1")


def test_health_reports_version_and_data_dir(tmp_path):
    r = _client(tmp_path).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["data_dir"] == str(tmp_path / "data")


def test_index_page_is_served(tmp_path):
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "One Production" in r.text
