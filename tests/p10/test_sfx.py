"""Sound effects from Epidemic Sound (owner, 2026-10-11): the key in Credential Manager (a memory stand-in
here), search / fetch with downloads counted and reused, credits, the tools' policy, timing + pre-gain
of sfx.json, and placement on A3. A stand-in Epidemic API answers; no real key or network."""
import json

import httpx
import pytest

from app import sfx as X
from app.agent.policy import decide
from app.fusion import Fusion, FusionError, check_sfx
from app.storage import Store
from tests.p10.media_fixtures import FF, ff, master, needs_ffmpeg, srt, voice

KEY = "epidemic_live_TestKey0123456789abcdef"
WHOOSH = "0b7e5c3a-1111-4c4c-9a9a-0123456789ab"
CLICK = "1c8f6d4b-2222-4d4d-8b8b-0123456789ab"
HITS: list[str] = []
MP3 = {}


def epidemic(request: httpx.Request) -> httpx.Response:
    HITS.append(str(request.url))
    if request.url.host == "cdn.epidemic.test":
        return httpx.Response(200, content=MP3.get("bytes", b"ID3" + b"\x00" * 1024))
    if request.url.host != "partner-content-api.epidemicsound.com":
        return httpx.Response(599)
    if request.headers.get("Authorization") != f"Bearer {KEY}":
        return httpx.Response(401, json={"message": "unauthorized"})
    assert request.headers.get("x-partner-user-id")
    path = request.url.path
    if path == "/v0/sound-effects/search":
        if request.url.params.get("term") == "toomany":
            return httpx.Response(429)
        return httpx.Response(200, json={"soundEffects": [
            {"id": WHOOSH, "title": "Whoosh fast", "added": "2024-01-01T00:00:00Z", "length": 1},
            {"id": CLICK, "title": "Long ambience", "added": "2024-01-01T00:00:00Z", "length": 40}],
            "pagination": {"limit": 10, "page": 1}, "links": {}})
    if path.startswith("/v0/sound-effects/") and path.endswith("/download"):
        assert request.url.params.get("format") == "mp3"
        return httpx.Response(200, json={"url": f"https://cdn.epidemic.test/{path.split('/')[3]}.mp3", "expires": "x"})
    return httpx.Response(404)


@pytest.fixture
def svc(tmp_path):
    HITS.clear()
    store = Store(tmp_path / "data")
    pid = store.create_project("S")["id"]
    return X.Sfx(store, tmp_path / "data", transport=httpx.MockTransport(epidemic)), pid, store


def test_key_is_checked_then_kept_in_the_credential_store(svc, memory_keyring):
    s, _, _ = svc
    with pytest.raises(X.SfxError, match="starts with epidemic_live_"):
        s.set_key("sk-ant-api03-not-this-one")
    with pytest.raises(X.SfxError, match="refused the key"):
        s.set_key("epidemic_live_WrongKey123456")
    st = s.set_key(" " + KEY + " ")
    assert st["key_set"] and st["key_hint"] == "…" + KEY[-4:] and "prototyping" in st["licence_note"]
    assert memory_keyring.store[("OneProduction", "epidemic")] == KEY
    assert KEY not in json.dumps(st) and KEY not in (s.state_file.read_text(encoding="utf-8"))
    assert not s.remove_key()["key_set"]


def test_without_a_key_the_tools_say_how_to_add_one(svc):
    with pytest.raises(X.SfxError, match="No Epidemic Sound key yet"):
        svc[0].search("whoosh")


def test_search_then_fetch_once_and_reuse(svc, memory_keyring):
    s, pid, store = svc
    s.set_key(KEY)
    r = s.search("whoosh", max_seconds=5)
    assert [x["id"] for x in r["results"]] == [WHOOSH] and r["results"][0]["cached"] is False
    a = s.fetch(pid, WHOOSH, "Whoosh fast")
    assert a == {"file": f"sfx/epidemic-{WHOOSH}.mp3", "title": "Whoosh fast", "downloaded": True}
    assert (store.files_dir(pid) / a["file"]).is_file() and s.status()["downloads"] == 1
    other = store.create_project("T")["id"]
    b = s.fetch(other, WHOOSH, "Whoosh fast")                     # another episode: no second download
    assert b["downloaded"] is False and s.status()["downloads"] == 1
    assert sum("/download" in h for h in HITS) == 1
    credits = (store.files_dir(pid) / "sfx" / "CREDITS.md").read_text(encoding="utf-8")
    assert "Epidemic Sound" in credits and "Whoosh fast" in credits
    assert s.search("whoosh")["results"][0]["cached"] is True
    with pytest.raises(X.SfxError, match="id must be"):
        s.fetch(pid, "../../etc")
    with pytest.raises(X.SfxError, match="limit is used up"):
        s.search("toomany")


def test_the_tools_need_no_card(tmp_path):
    for t in ("sfx_search", "sfx_fetch"):
        assert decide(f"mcp__studio__{t}", {}, tmp_path)[0] == "allow"


def test_sfx_json_rules():
    assert check_sfx([{"file": "sfx/a.mp3", "shot": "shot-002", "gain_db": -60}])[0]["gain_db"] == -40.0
    for bad, why in (([{"file": "music/a.mp3", "shot": "shot-001"}], "in sfx/"), ([{"file": "sfx/a.mp3"}], "needs"),
                     ({"x": 1}, "must be a list"), ([{"file": "sfx/a.mp3", "shot": "s", "offset_s": "soon"}], "numbers")):
        with pytest.raises(FusionError, match=why):
            check_sfx(bad)


@needs_ffmpeg
def test_effects_are_timed_on_cuts_and_words_and_pre_gained(tmp_path):
    store = Store(tmp_path / "data")
    pid = store.create_project("F")["id"]
    files = store.files_dir(pid)
    master(voice(tmp_path / "raw.wav", 120, 8), files / "vo.wav")
    srt(files / "vo.srt", 8, [(0, 4, "first the gold standard ended"), (4, 8, "then the dollar floated freely")])
    (files / "sfx").mkdir()
    ff("-f", "lavfi", "-i", "sine=frequency=1000:duration=0.3", "-ar", "44100", files / "sfx" / "click.mp3")
    (files / "work").mkdir()
    (files / "work" / "sfx.json").write_text(json.dumps([{"file": "sfx/click.mp3", "shot": "shot-002"},
                                                          {"file": "sfx/click.mp3", "anchor": "floated freely", "offset_s": -0.1, "gain_db": -6}]),
                                             encoding="utf-8")
    svc = Fusion(store, tmp_path, None, None)
    shots = [{"name": "shot-001", "start": 0, "frames": 300}, {"name": "shot-002", "start": 300, "frames": 180}]
    out = svc.sfx_plan(pid, "work/sfx.json", FF, shots, [{"start": 0, "end": 4, "text": "first the gold standard ended"},
                                                          {"start": 4, "end": 8, "text": "then the dollar floated freely"}], 8.0, files / "work")
    assert [o["frame"] for o in out] == [300, int(round((4 + 4 * 3 / 5 - 0.1) * 60))]
    assert all(o["path"].endswith(".wav") for o in out) and (files / "work" / "sfx" / "001.wav").is_file()
    (files / "work" / "sfx.json").write_text(json.dumps([{"file": "sfx/click.mp3", "shot": "shot-009"}]), encoding="utf-8")
    with pytest.raises(FusionError, match="no shot-009"):
        svc.sfx_plan(pid, "work/sfx.json", FF, shots, [], 8.0, files / "work")
