"""Updater edge cases, migrations, diagnostics and the sample project - fast, no real package."""
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from app import migrate as M
from app.installer import InstallError
from app.update import Updater, parse_version


def fake_install(root: Path, current="0.8.0", source=None):
    for v in [current]:
        add_version(root, v)
    (root / "current.txt").write_text(current)
    (root / "install.json").write_text(json.dumps({"current": current, "previous": None, "update_source": source}))
    return root


def add_version(root: Path, v: str):
    d = root / "versions" / v
    (d / "python").mkdir(parents=True, exist_ok=True)
    (d / "python" / "python.exe").write_bytes(b"MZ")
    (d / "app").mkdir(exist_ok=True)
    (d / "app" / "__init__.py").write_text("")
    (d / "VERSION").write_text(v)


def release(folder: Path, v: str, *, version_file=None, extra=None, missing_python=False, tamper=False):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        base = f"OneProduction/versions/{v}/"
        if not missing_python:
            z.writestr(base + "python/python.exe", b"MZ")
        z.writestr(base + "app/__init__.py", "")
        z.writestr(base + "VERSION", version_file or v)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    data = buf.getvalue()
    folder.mkdir(parents=True, exist_ok=True)
    name = f"OneProduction-{v}-win64.zip"
    (folder / name).write_bytes(data + (b"x" if tamper else b""))
    (folder / "latest.json").write_text(json.dumps({"version": v, "file": name, "size": len(data),
                                                    "sha256": hashlib.sha256(data).hexdigest(), "notes": "new"}))


def wait(u: Updater):
    u.thread.join(20)
    return u.state


def test_versions_compare_as_numbers():
    assert parse_version("0.10.0") > parse_version("0.9.9") > parse_version("0.9")


def test_development_copy_never_updates_itself(tmp_path):
    u = Updater(root=None)
    assert u.info()["packaged"] is False and "development copy" in u.info()["message"]
    assert u.check()["newer"] is False
    with pytest.raises(InstallError):
        u.set_source(str(tmp_path))


def test_update_from_a_folder_then_rollback_keeps_two_versions(tmp_path, monkeypatch):
    import app.update as U
    monkeypatch.setattr(U, "__version__", "0.8.0")                     # the version that's running
    root = fake_install(tmp_path / "install")
    u = Updater(root)
    assert u.check()["message"].startswith("No update source")
    with pytest.raises(InstallError):
        u.set_source("relative/folder")
    u.set_source(str(tmp_path / "rel"))
    release(tmp_path / "rel", "0.8.1")
    c = u.check()
    assert c["newer"] and c["latest"] == "0.8.1" and c["notes"] == "new"
    u.start_download()
    assert wait(u)["ready"] == "0.8.1"
    info = u.apply()
    assert info["current"] == "0.8.1" and info["previous"] == "0.8.0"
    assert (root / "current.txt").read_text() == "0.8.1"
    monkeypatch.setattr(U, "__version__", "0.8.1")                     # after the restart into 0.8.1
    release(tmp_path / "rel", "0.8.2")
    u.start_download(); wait(u); u.apply()
    assert sorted(u.info()["versions"]) == ["0.8.1", "0.8.2"]           # 0.8.0 pruned, two kept
    back = u.rollback()
    assert back["current"] == "0.8.1" and back["previous"] == "0.8.2"


def test_nothing_newer_and_no_previous(tmp_path):
    root = fake_install(tmp_path / "install", current="0.9.0", source=str(tmp_path / "rel"))
    release(tmp_path / "rel", "0.8.5")
    u = Updater(root)
    assert u.check()["newer"] is False
    with pytest.raises(InstallError, match="newest"):
        u.start_download()
    with pytest.raises(InstallError, match="no previous"):
        u.rollback()


@pytest.mark.parametrize("kw, why", [
    (dict(tamper=True), "damaged"),
    (dict(missing_python=True), "incomplete"),
    (dict(version_file="0.7.0"), "different version"),
    (dict(extra={"OneProduction/versions/0.8.1/../../../evil.txt": b"x"}), "unsafe"),
])
def test_bad_updates_are_refused_and_nothing_switches(tmp_path, kw, why):
    root = fake_install(tmp_path / "install", source=str(tmp_path / "rel"))
    release(tmp_path / "rel", "0.8.1", **kw)
    u = Updater(root)
    u.start_download()
    st = wait(u)
    assert why in (st.get("error") or ""), st
    assert (root / "current.txt").read_text() == "0.8.0" and not (root / "versions" / "0.8.1").exists()
    assert not (tmp_path / "evil.txt").exists()
    with pytest.raises(InstallError):
        u.apply()


def test_update_over_http_resumes(tmp_path):
    from tests.p7.conftest import Files
    import threading
    from http.server import ThreadingHTTPServer
    Files.files, Files.log = {}, []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Files)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        rel = tmp_path / "rel"
        release(rel, "0.8.1")
        for f in rel.iterdir():
            Files.files["/r/" + f.name] = f.read_bytes()
        root = fake_install(tmp_path / "install", source=f"http://127.0.0.1:{srv.server_address[1]}/r/")
        u = Updater(root)
        assert u.check()["file"].endswith("/r/OneProduction-0.8.1-win64.zip")
        u.start_download()
        assert wait(u)["ready"] == "0.8.1"
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- migrations
def test_data_folder_is_migrated_once_and_rollback_is_tolerated(tmp_path, monkeypatch):
    r = M.migrate(tmp_path)
    assert r["from"] == 0 and r["to"] == M.SCHEMA and r["ran"] == list(range(1, M.SCHEMA + 1))
    assert M.migrate(tmp_path)["ran"] == []                                    # idempotent
    (tmp_path / "version.json").write_text(json.dumps({"schema": M.SCHEMA + 3, "app": "9.0.0"}))
    r = M.migrate(tmp_path)                                                    # an older app after a rollback
    assert r["newer_data"] is True and json.loads((tmp_path / "version.json").read_text())["schema"] == M.SCHEMA + 3


# ---------------------------------------------------------------- through the app
def test_sample_project_once_and_ready_to_voice(make_client):
    c = make_client()
    a = c.post("/api/sample").json()
    b = c.post("/api/sample").json()
    assert a["id"] == b["id"] and a["sample"] is True
    names = [f["name"] for f in c.get(f"/api/projects/{a['id']}/files").json()]
    assert "Start here.md" in names and "Sample script - Ghost towns.md" in names
    stats = c.post(f"/api/projects/{a['id']}/script-stats", json={"file": "Sample script - Ghost towns.md"}).json()
    assert 80 <= stats["words"] <= 140 and stats["sections"] == 2                 # the note line isn't spoken


def test_updates_and_shortcuts_in_a_development_copy(make_client):
    c = make_client()
    u = c.get("/api/update").json()
    assert u["packaged"] is False and u["migration"]["to"] == M.SCHEMA
    assert c.post("/api/shortcuts").status_code == 400


def test_diagnostics_hold_no_secrets_and_no_chat_text(make_client):
    from tests.p2.conftest import GOOD_KEY
    c = make_client()
    pid = c.post("/api/projects", json={"name": "Private project"}).json()["id"]
    c.post(f"/api/projects/{pid}/chat", json={"message": f"secret plan {GOOD_KEY}", "account_id": "demo"})
    (c.data_dir / "logs" / "app.log").write_text(f"boom api_key={GOOD_KEY} for someone@example.com\n")
    r = c.get("/api/diagnostics")
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        blob = b"".join(z.read(n) for n in z.namelist())
        log = z.read("app.log").decode()
    assert GOOD_KEY.encode() not in blob and b"secret plan" not in blob and b"Private project" not in blob
    assert "s***@example.com" in log and "someone@" not in log
