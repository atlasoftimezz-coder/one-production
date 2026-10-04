"""The chat assistant can propose a voiceover and the user runs it with one click.

User report: "Add 15 WPM" got "I can't invoke tools" - and the reply opened with "Caveman mode
on", i.e. the user's own Claude Code instructions leaked into the studio assistant.
"""
import json
import sys

import httpx
import keyring
import pytest
from fastapi.testclient import TestClient

from app import actions
from app.config import Settings
from app.main import create_app
from app.security.guard import TOKEN_HEADER
from tests.p2.conftest import FAKE, MemoryKeyring, signed_in

from .conftest import TOKEN, wait_job

FILES = [{"name": "ep1.md"}, {"name": "intro.wav"}]
VOICES = ["buster_ref48.wav", "human_ref48.wav"]
PRESETS = {"clone_exact": {"label": "Clone the reference exactly"}, "narrator": {"label": "Narrator"}}


def block(**a):
    return "Sure - 165 wpm with Buster.\n\n```action\n" + json.dumps({"tool": "voiceover", **a}) + "\n```"


# ---------------------------------------------------------------- the pure parts
def test_reply_without_a_block_is_left_alone():
    assert actions.extract("Just chatting.") == ("Just chatting.", None)


def test_block_is_taken_out_of_the_reply_and_checked():
    text, a = actions.extract(block(file="ep1.md", voice="buster_ref48.wav", pace_mode="wpm", target_wpm=165))
    assert text == "Sure - 165 wpm with Buster." and "```" not in text
    assert actions.check(a, FILES, VOICES, PRESETS) == {
        "tool": "voiceover", "file": "ep1.md", "voice": "buster_ref48.wav", "pace_mode": "wpm",
        "preset": "clone_exact", "target_wpm": 165}


@pytest.mark.parametrize("bad, why", [
    (dict(file="nope.md", voice="buster_ref48.wav"), "no file"),
    (dict(file="ep1.md", voice="ghost.wav"), "no voice"),
    (dict(file="ep1.md", voice="buster_ref48.wav", pace_mode="warp"), "unknown pace"),
    (dict(file="ep1.md", voice="buster_ref48.wav", pace_mode="wpm"), "no number"),
    (dict(file="ep1.md", voice="buster_ref48.wav", pace_mode="wpm", target_wpm=400), "outside"),
    (dict(tool="shell", file="ep1.md", voice="buster_ref48.wav"), "unknown tool"),
])
def test_bad_proposals_are_explained_not_run(bad, why):
    _, a = actions.extract(block(**bad))
    assert why in actions.check(a, FILES, VOICES, PRESETS)["error"]


def test_unreadable_block_is_reported():
    _, a = actions.extract("ok\n```action\n{not json\n```")
    assert "couldn't read" in actions.check(a, FILES, VOICES, PRESETS)["error"]


def test_context_gives_the_numbers_needed_for_add_15_wpm():
    jobs = [{"name": "ep1_buster", "source": "ep1.md", "voice": "buster_ref48.wav", "preset": "clone_exact",
             "pace_mode": None, "status": "done", "result": {"wpm": 150, "clock": "12:58"}}]
    ctx = actions.context(FILES, VOICES, PRESETS, jobs)
    assert "ep1.md" in ctx and "buster_ref48.wav" in ctx and "measured 150 wpm" in ctx
    assert "intro.wav" in ctx.split("Other files:")[1]
    assert "not running" in actions.context(FILES, None, {}, [])


# ---------------------------------------------------------------- through the app
@pytest.fixture
def studio(tmp_path, fake_voice, voice_dir, monkeypatch, request):
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    request.addfinalizer(lambda: keyring.set_keyring(previous))
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for tool in ("claude", "codex"):
        (bin_ / f"{tool}.cmd").write_text(f'@"{sys.executable}" "{FAKE}" {tool} %*\r\n', encoding="utf-8")
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "calls.txt"))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "prompts.jsonl"))
    for k in ("FAKE_MODE", "FAKE_FAILS", "FAKE_UTIL", "FAKE_REPLY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("app.voice.POLL", 0.05)
    settings = Settings(data_dir=tmp_path / "data", claude_cmd=str(bin_ / "claude.cmd"),
                        codex_cmd=str(bin_ / "codex.cmd"), reply_timeout=20,
                        voice_app_dir=str(voice_dir), voice_python=sys.executable, agent_mode=False)
    c = TestClient(create_app(settings, token=TOKEN, voice_transport=httpx.MockTransport(fake_voice.handler),
                              login_console=False), base_url="http://127.0.0.1")
    c.__enter__()
    request.addfinalizer(lambda: c.__exit__(None, None, None))
    c.headers[TOKEN_HEADER] = TOKEN
    c.data_dir, c.fake, c.log = tmp_path / "data", fake_voice, tmp_path / "prompts.jsonl"
    c.get("/api/voice/status")                  # what opening the voice dialog does
    c.pid = c.post("/api/projects", json={"name": "Debt"}).json()["id"]
    c.post(f"/api/projects/{c.pid}/files", files={"files": ("ep1.md", b"## Debt\nCancel the debt today.\n")})
    return c


def ask(c, acc, message):
    r = c.post(f"/api/projects/{c.pid}/chat", json={"message": message, "account_id": acc["id"]})
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def last_call(c):
    return json.loads(c.log.read_text(encoding="utf-8").splitlines()[-1])


def test_assistant_sees_the_studio_and_users_own_claude_setup_stays_out(studio, monkeypatch):
    acc = signed_in(studio, "claude", monkeypatch=monkeypatch)
    ask(studio, acc, "Add 15 WPM")
    call = last_call(studio)
    assert "ep1.md" in call["prompt"] and "buster_ref48.wav" in call["prompt"] and "```action" in call["prompt"]
    assert call["env"].get("CLAUDE_CODE_DISABLE_CLAUDE_MDS") == "1"          # no CLAUDE.md from parent folders
    assert call["env"].get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") == "1"
    assert "--disable-slash-commands" in call["args"]


def test_proposal_becomes_a_run_card_and_running_it_starts_the_voiceover(studio, monkeypatch):
    acc = signed_in(studio, "claude", monkeypatch=monkeypatch)
    monkeypatch.setenv("FAKE_REPLY", block(file="ep1.md", voice="buster_ref48.wav", pace_mode="wpm", target_wpm=165))
    events = ask(studio, acc, "Voice it with Buster, a bit faster")
    act = next(e for e in events if e["t"] == "action")
    assert act["text"] == "Sure - 165 wpm with Buster." and act["action"]["target_wpm"] == 165
    saved = studio.get(f"/api/projects/{studio.pid}/chat").json()[-1]
    assert saved["text"] == "Sure - 165 wpm with Buster." and saved["action"] == act["action"]
    assert studio.fake.specs == []                                             # nothing runs by itself

    a = act["action"]
    r = studio.post(f"/api/projects/{studio.pid}/voice", json={**a, "approve": None, "from_action": act["time"]})
    assert r.status_code == 409                                                # GPU gate still asks
    r = studio.post(f"/api/projects/{studio.pid}/voice", json={**a, "approve": "once", "from_action": act["time"]})
    job = wait_job(studio, r.json()["id"])
    assert job["status"] == "done" and studio.fake.specs[-1]["target_wpm"] == 165
    req = next(m for m in studio.get(f"/api/projects/{studio.pid}/chat").json() if m.get("from_action"))
    assert req["from_action"] == act["time"] and req["text"].endswith("at 165 wpm")   # card shows "Started"

    # the next request sees what that render really did
    ask(studio, acc, "now 10 slower")
    assert "pace asked wpm 165 wpm; measured 157 wpm" in last_call(studio)["prompt"]


def test_impossible_proposal_shows_why(studio, monkeypatch):
    acc = signed_in(studio, "claude", monkeypatch=monkeypatch)
    monkeypatch.setenv("FAKE_REPLY", block(file="ep1.md", voice="morgan.wav"))
    act = next(e for e in ask(studio, acc, "use Morgan") if e["t"] == "action")
    assert "no voice called 'morgan.wav'" in act["action"]["error"]
