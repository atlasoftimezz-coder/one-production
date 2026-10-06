"""Stock footage (P10 add-on, owner 2026-10-06): Pexels search / fetch with the owner's key, the key
kept in Credential Manager (a memory stand-in here), credits, the tools' policy, and stock clips in
shots. A stand-in Pexels answers every request; no real key or network is used."""
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import stock as S
from app.agent.policy import decide
from app.config import Settings
from app.main import create_app
from app.security.guard import TOKEN_HEADER
from tests.p10.media_fixtures import master, needs_ffmpeg, png, srt, video, voice

KEY = "PexelsTestKey0123456789abcdefABCDEF0123456789abcdefABCD"
HITS: list[str] = []


def vfile(w, h, q="hd", kind="video/mp4", host="videos.pexels.com", n=1):
    return {"id": n, "quality": q, "file_type": kind, "width": w, "height": h, "fps": 25,
            "link": f"https://{host}/video-files/{w}x{h}.mp4"}


RIVER = {"id": 1234, "width": 3840, "height": 2160, "duration": 12, "url": "https://www.pexels.com/video/aerial-view-of-a-river-1234/",
         "image": "https://images.pexels.com/videos/1234/x.jpeg", "user": {"name": "Ana Lee", "url": "https://www.pexels.com/@ana"},
         "video_files": [vfile(640, 360, "sd"), vfile(1920, 1080), vfile(1280, 720), vfile(3840, 2160, "uhd"), vfile(1920, 1080, kind="video/webm")]}
TINY = {"id": 99, "duration": 30, "url": "https://www.pexels.com/video/small-99/", "user": {"name": "B"},
        "video_files": [vfile(640, 360, "sd")]}
SHORT = {**RIVER, "id": 77, "duration": 3, "url": "https://www.pexels.com/video/short-clip-77/"}
EVIL = {**RIVER, "id": 666, "video_files": [vfile(1920, 1080, host="evil.example.com")]}
PHOTO = {"id": 55, "width": 4000, "height": 3000, "url": "https://www.pexels.com/photo/red-fox-in-snow-55/", "alt": "Red fox in snow",
         "photographer": "Cam Roe", "photographer_url": "https://www.pexels.com/@cam",
         "src": {"original": "https://images.pexels.com/photos/55/pexels-photo-55.jpeg", "large2x": "x"}}
SMALL_PHOTO = {**PHOTO, "id": 56, "width": 640, "height": 480}


def pexels(request: httpx.Request) -> httpx.Response:
    HITS.append(str(request.url))
    host, path = request.url.host, request.url.path
    if host in ("videos.pexels.com", "images.pexels.com"):
        return httpx.Response(200, content=b"\x00" * 2048)
    if host != "api.pexels.com":
        return httpx.Response(599)
    if request.headers.get("Authorization") != KEY:
        return httpx.Response(401, json={"error": "bad key"})
    if request.url.params.get("query") == "toomany":
        return httpx.Response(429)
    rate = {"X-Ratelimit-Limit": "200", "X-Ratelimit-Remaining": "187", "X-Ratelimit-Reset": "1700000000"}
    if path == "/videos/search":
        return httpx.Response(200, headers=rate, json={"videos": [RIVER, TINY, SHORT], "page": 1})
    if path == "/v1/search":
        return httpx.Response(200, headers=rate, json={"photos": [SMALL_PHOTO, PHOTO], "page": 1})
    for v in (RIVER, TINY, EVIL):
        if path == f"/videos/videos/{v['id']}":
            return httpx.Response(200, headers=rate, json=v)
    if path == "/v1/photos/55":
        return httpx.Response(200, headers=rate, json=PHOTO)
    return httpx.Response(404)


class Store:
    def __init__(self, root):
        self.root = root

    def files_dir(self, pid):
        return self.root / pid / "files"


@pytest.fixture
def stock(tmp_path):
    HITS.clear()
    return S.Stock(Store(tmp_path), transport=httpx.MockTransport(pexels))


def test_key_is_checked_then_kept_in_the_credential_store(stock, memory_keyring):
    with pytest.raises(S.StockError, match="doesn't look like"):
        stock.set_key("not a key!")
    with pytest.raises(S.StockError, match="refused the key"):
        stock.set_key("WrongKey0123456789abcdefghij")
    assert not stock.status()["key_set"]
    st = stock.set_key("  " + KEY + " ")
    assert st == {"provider": "pexels", "key_set": True, "key_hint": "…" + KEY[-4:], "rate": st["rate"]}
    assert memory_keyring.store[("OneProduction", "pexels")] == KEY
    assert not stock.remove_key()["key_set"] and ("OneProduction", "pexels") not in memory_keyring.store


def test_without_a_key_the_tools_say_how_to_add_one(stock):
    with pytest.raises(S.StockError, match="No Pexels key yet"):
        stock.search("river")


def test_video_search_keeps_720p_to_1080p_mp4s_and_says_what_they_show(stock):
    stock.set_key(KEY)
    r = stock.search("river", min_seconds=5)
    assert [x["id"] for x in r["results"]] == [1234]            # TINY has no HD file, SHORT is 3 s
    hit = r["results"][0]
    assert hit["about"] == "aerial view of a river" and hit["size"] == "1920x1080" and hit["by"] == "Ana Lee"
    assert r["requests_left"] == "187"
    assert "size=medium" in HITS[-1] and "orientation=landscape" in HITS[-1]


def test_photo_search_drops_small_photos(stock):
    stock.set_key(KEY)
    r = stock.search("fox", kind="photo")
    assert [(x["id"], x["about"]) for x in r["results"]] == [(55, "Red fox in snow")]


def test_search_rejects_bad_arguments_and_reports_the_limit(stock):
    stock.set_key(KEY)
    for args, msg in ((("",), "what to search"), (("x", "audio"), "video or photo"), (("x", "video", "diagonal"), "orientation")):
        with pytest.raises(S.StockError, match=msg):
            stock.search(*args)
    with pytest.raises(S.StockError, match="request limit"):
        stock.search("toomany")


def test_fetch_downloads_once_into_stock_and_credits_it(stock, tmp_path):
    stock.set_key(KEY)
    out = stock.fetch("p1", "video", 1234)
    f = tmp_path / "p1" / "files" / "stock" / "pexels-video-1234.mp4"
    assert out["file"] == "stock/pexels-video-1234.mp4" and f.stat().st_size == 2048
    assert out["size"] == "1920x1080" and out["credit"] == "Video by Ana Lee on Pexels"
    assert any("1920x1080.mp4" in h for h in HITS)
    downloads = sum("videos.pexels.com" in h for h in HITS)
    stock.fetch("p1", "video", "1234")                           # again: the file is reused
    assert sum("videos.pexels.com" in h for h in HITS) == downloads
    credits = (f.parent / "CREDITS.md").read_text(encoding="utf-8")
    assert "Video by Ana Lee on Pexels: https://www.pexels.com/video/aerial-view-of-a-river-1234/" in credits
    assert not list(f.parent.glob("*.part"))


def test_photo_is_fetched_at_a_sensible_width(stock, tmp_path):
    stock.set_key(KEY)
    out = stock.fetch("p1", "photo", 55)
    assert out["file"] == "stock/pexels-photo-55.jpg"
    assert any(h.startswith("https://images.pexels.com/photos/55/pexels-photo-55.jpeg?") and "w=2560" in h for h in HITS)
    data = json.loads((tmp_path / "p1" / "files" / "stock" / "credits.json").read_text(encoding="utf-8"))
    assert data["stock/pexels-photo-55.jpg"]["by"] == "Cam Roe"


def test_fetch_refuses_odd_hosts_and_files_without_hd(stock):
    stock.set_key(KEY)
    with pytest.raises(S.StockError, match="somewhere unexpected"):
        stock.fetch("p1", "video", 666)
    with pytest.raises(S.StockError, match="no mp4 between"):
        stock.fetch("p1", "video", 99)
    with pytest.raises(S.StockError, match="number"):
        stock.fetch("p1", "video", "../../x")
    assert S.allowed_download("https://videos.pexels.com/a.mp4") and not S.allowed_download("http://videos.pexels.com/a.mp4")
    assert not S.allowed_download("https://pexels.com.evil.net/a.mp4")


def test_stock_tools_need_no_card_and_appear_only_with_a_key(stock, tmp_path):
    from app.agent.studio import Studio
    assert decide("mcp__studio__stock_search", {}, tmp_path)[0] == "allow"
    assert decide("stock_fetch", {"kind": "video", "id": 1}, tmp_path)[0] == "allow"
    names = lambda: {t.name for t in Studio("p1", None, None, None, lambda j: None, stock=stock).tools()}
    assert "stock_search" not in names()
    stock.set_key(KEY)
    assert {"stock_search", "stock_fetch"} <= names()


def test_api_sets_the_key_and_the_module_turns_on(tmp_path, fake_cmds, memory_keyring):
    app = create_app(Settings(data_dir=tmp_path / "data", claude_cmd=str(fake_cmds / "claude.cmd"),
                              codex_cmd=str(fake_cmds / "codex.cmd"), agent_mode=False),
                     token="t" * 32, login_console=False, stock_transport=httpx.MockTransport(pexels),
                     gpu_probe=lambda: None)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers[TOKEN_HEADER] = "t" * 32
        mod = lambda: next(m for m in c.get("/api/modules").json()["modules"] if m["id"] == "stock")
        assert mod()["state"] == "off"
        assert c.post("/api/stock/key", json={"key": "WrongKey0123456789abcdefghij"}).status_code == 400
        r = c.post("/api/stock/key", json={"key": KEY})
        assert r.status_code == 200 and KEY not in r.text
        m = mod()
        assert m["state"] == "ready" and m["key_hint"] == "…" + KEY[-4:] and KEY not in json.dumps(m)
        assert KEY not in c.get("/api/stock").text
        assert c.delete("/api/stock/key").json()["key_set"] is False and mod()["state"] == "off"


@needs_ffmpeg
def test_stock_clips_and_photos_mix_into_shots(client, tmp_path):
    pid = client.post("/api/projects", json={"name": "Stock"}).json()["id"]
    files = client.data_dir / "projects" / pid / "files"
    (files / "stock").mkdir(parents=True)
    master(voice(tmp_path / "raw.wav", 120, 9), files / "vo.wav")
    srt(files / "vo.srt", 9, [(0, 3, "a river runs here"), (3, 6, "then a fox appears"), (6, 9, "and that is all")])
    video(files / "stock" / "pexels-video-1.mp4", files / "vo.wav", 4)
    png(files / "stock" / "pexels-photo-2.png", 1600, 900)
    shots = {"voiceover": "vo.wav", "subtitles": "vo.srt", "size": "1280x720", "output": "mix.mp4",
             "shots": [{"file": "stock/pexels-video-1.mp4", "from": 0.5},
                       {"file": "stock/pexels-photo-2.png", "anchor": "a fox appears", "motion": "pan_left"},
                       {"card": {"text": "The end", "style": "title"}, "anchor": "that is all"}]}
    (files / "shots.json").write_text(json.dumps(shots), encoding="utf-8")
    r = client.post(f"/api/projects/{pid}/animation/build", json={"shots": "shots.json"})
    assert r.status_code == 200, r.text
    edit = json.loads((files / "shots.edit.json").read_text())
    assert edit["visuals"][0] == {"seconds": pytest.approx(3.6, abs=0.3), "file": "stock/pexels-video-1.mp4", "from": 0.5}
    assert edit["visuals"][1]["motion"] == "pan_left" and "from" not in edit["visuals"][1]
    job = client.post(f"/api/projects/{pid}/edit/render", json={"edit": "shots.edit.json", "approve": "once"}).json()
    for _ in range(600):
        job = client.get(f"/api/projects/{pid}/edit/jobs/{job['id']}").json()
        if job["status"] != "rendering":
            break
        time.sleep(0.3)
    assert job["status"] == "done" and job["gate"]["passed"], job
    shots["shots"][0] = {"file": "vo.srt"}
    (files / "shots.json").write_text(json.dumps(shots), encoding="utf-8")
    assert "not a video clip" in client.post(f"/api/projects/{pid}/animation/build", json={"shots": "shots.json"}).json()["detail"]
    shots["shots"][0] = {"file": "stock/pexels-video-1.mp4", "svg": "x.svg"}
    (files / "shots.json").write_text(json.dumps(shots), encoding="utf-8")
    assert "exactly one of" in client.post(f"/api/projects/{pid}/animation/build", json={"shots": "shots.json"}).json()["detail"]
