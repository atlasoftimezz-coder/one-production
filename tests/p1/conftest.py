import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security.guard import TOKEN_HEADER

TOKEN = "test-session-token-0123456789abcdef"


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data"), token=TOKEN)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers[TOKEN_HEADER] = TOKEN
    c.data_dir = tmp_path / "data"
    return c


@pytest.fixture
def anon(tmp_path):
    """Same app, but no token - like any other page or program on this PC."""
    app = create_app(Settings(data_dir=tmp_path / "data"), token=TOKEN)
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture
def project(client):
    return client.post("/api/projects", json={"name": "Test project"}).json()
