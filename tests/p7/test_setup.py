"""Setup wizard back end (plan R13/R14): modules with honest states, the install plan, the
first-run folder choice, an API-key-only friend, and voice tuning."""
import json
import sys
import time

import pytest

from tests.p2.conftest import GOOD_KEY

from .conftest import GTX1060, RTX5060TI


def mods(c):
    return {m["id"]: m for m in c.get("/api/modules").json()["modules"]}


def test_without_nvidia_voice_is_offered_on_the_processor_or_off(make_app):
    c = make_app(gpu=None)
    v = mods(c)["voice"]
    assert v["suggested"] == "off" and v["mode"] == "off" and v["state"] == "off"
    assert "20 times slower" in v["needs"] and "processor" in v["warning"]
    st = c.get("/api/voice/status").json()
    assert st["configured"] is False and "turned off" in st["missing"][0]
    assert c.patch("/api/modules/voice", json={"mode": "cpu"}).status_code == 200
    plan = c.get("/api/install/plan").json()
    torch = next(i for i in plan["items"] if i["id"] == "torch")
    assert plan["torch_build"] == "cpu" and "processor only" in torch["label"]


def test_small_card_is_not_the_default_either(make_app):
    v = mods(make_app(gpu=GTX1060))["voice"]
    assert v["suggested"] == "off" and v["gpu_ok"] is False


def test_rtx_5060_ti_gets_the_cuda_12_8_build_and_nothing_runs_without_ok(make_app):
    c = make_app(gpu=RTX5060TI)
    v = mods(c)["voice"]
    assert v["suggested"] == "gpu" and v["state"] == "needs_install"
    plan = c.get("/api/install/plan").json()
    assert plan["torch_build"] == "cu128"
    assert [i["id"] for i in plan["items"]] == ["ffmpeg", "python", "torch", "engine", "models"]
    assert all(i["size_mb"] > 0 and i["source"] and i["why"] for i in plan["items"])
    r = c.post("/api/install", json={"approve": []})
    assert r.status_code == 400 and "needs your OK" in r.json()["detail"]
    assert c.get("/api/install").json()["running"] is False


def test_later_modules_are_greyed_with_what_they_will_need(make_app):
    m = mods(make_app())
    assert m["core"]["state"] == "ready"
    for later in ("subtitles", "animation", "video", "research"):
        assert m[later]["state"] == "later" and m[later]["needs"] and "Phase 2" in m[later]["detail"]
    # Stock is built (Pexels, 2026-10-06): off until the owner adds a key, never greyed
    assert m["stock"]["state"] == "off" and "Pexels" in m["stock"]["needs"] and not m["stock"]["key_set"]


def test_your_own_voice_app_counts_as_ready(make_app):
    c = make_app(external=True)
    v = mods(c)["voice"]
    assert v["state"] == "ready" and v["external"]
    assert c.get("/api/install/plan").json() == {"module": "voice", "external": True, "items": []}
    assert c.get("/api/voice/status").json()["configured"] is True


def test_half_installed_voice_module_is_never_ready(make_app):
    c = make_app(gpu=RTX5060TI)
    root = c.data_dir / "modules" / "voice"
    (root / "python").mkdir(parents=True)
    (root / "python" / "python.exe").write_bytes(b"")
    (root / "installed.json").write_text("{}")             # marker, but no models, no ffmpeg
    v = mods(c)["voice"]
    assert v["state"] == "broken" and "Install again" in v["detail"]
    assert c.get("/api/voice/status").json()["configured"] is False


# ---------------------------------------------------------------- the wizard's own state
def test_first_run_is_only_a_genuinely_new_install(make_app):
    c = make_app()
    s = c.get("/api/setup").json()
    assert s["first_run"] is True and s["done"] is False and s["steps"][0] == "welcome"
    assert s["drives"] and all("free_gb" in d for d in s["drives"])
    c.post("/api/projects", json={"name": "Existing work"})
    assert c.get("/api/setup").json()["first_run"] is False      # never interrupt someone already working
    assert c.patch("/api/setup", json={"step": 6, "done": True}).json()["done"] is True


def test_choosing_another_data_folder_saves_it_and_restarts(make_app):
    restarts = []
    c = make_app(restart=lambda: restarts.append(1))
    new = c.tmp / "D-drive" / "OneProduction"
    assert c.post("/api/setup/data-folder", json={"path": "relative\\folder"}).status_code == 400
    r = c.post("/api/setup/data-folder", json={"path": str(new)}).json()
    assert r["restarting"] is True and r["data_dir"] == str(new)
    assert json.loads((c.tmp / "settings.json").read_text())["data_dir"] == str(new)
    assert json.loads((new / "setup.json").read_text())["chose_folder"] is True     # wizard goes on there
    assert (new / "projects").is_dir()
    end = time.time() + 3
    while not restarts and time.time() < end:
        time.sleep(0.05)
    assert restarts == [1]


def test_folder_move_is_refused_once_there_is_data(make_app):
    c = make_app(restart=lambda: None)
    c.post("/api/projects", json={"name": "Existing work"})
    r = c.post("/api/setup/data-folder", json={"path": str(c.tmp / "elsewhere")})
    assert r.status_code == 409 and "isn't automatic" in r.json()["detail"]


def test_api_key_only_friend_without_claude_code_gets_a_working_chat(make_app):
    """The plan's fresh-PC path: no Claude Code, no Codex, no NVIDIA - just an API key."""
    c = make_app(gpu=None)
    system = {i["id"]: i for i in c.get("/api/system").json()["items"]}
    acc = c.post("/api/accounts", json={"provider": "anthropic_api", "api_key": GOOD_KEY}).json()
    assert acc["status"] == "ok"
    pid = c.post("/api/projects", json={"name": "First video"}).json()["id"]
    r = c.post(f"/api/projects/{pid}/chat", json={"message": "Give me a hook about Pripyat", "account_id": acc["id"]})
    events = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    text = "".join(e.get("v", "") for e in events if e["t"] == "text")
    assert text and not any(e["t"] == "error" for e in events)
    saved = c.get(f"/api/projects/{pid}/chat").json()[-1]
    assert saved["role"] == "assistant" and not saved.get("agent")          # plain chat: no Claude Code here
    assert system["voice"]["status"] in ("ok", "warn")
    assert c.patch("/api/setup", json={"done": True}).json()["done"] is True


# ---------------------------------------------------------------- tuning a voice
FAKE_TUNE = r'''
import argparse, json, pathlib, sys, time
ap = argparse.ArgumentParser(); ap.add_argument("--ref"); ap.add_argument("--out"); a = ap.parse_args()
for i in range(1, 4):
    print(f"STEP {i}/3 rendered take {i}", flush=True); time.sleep(0.05)
best = {"exaggeration": 0.65, "cfg_weight": 0.3, "temperature": 0.85}
pathlib.Path(a.out).write_text(json.dumps({"best": best, "device": "cuda", "ranking": [best], "seconds": 1}))
print(json.dumps(best))
'''


def test_tuning_a_voice_makes_its_settings_the_default(make_app, tmp_path):
    script = tmp_path / "tune.py"
    script.write_text(FAKE_TUNE, encoding="utf-8")
    c = make_app(external=True, tune_script=script)
    r = c.post("/api/voice/tune", json={"voice": "human_ref48.wav"})
    assert r.status_code == 409 and "3 minutes" in r.json()["needs_approval"]["uses"]   # GPU gate
    assert c.post("/api/voice/tune", json={"voice": "nobody.wav", "approve": "once"}).status_code == 404
    assert c.post("/api/voice/tune", json={"voice": "human_ref48.wav", "approve": "once"}).status_code == 200
    end = time.time() + 20
    while c.get("/api/voice/tune").json().get("running") and time.time() < end:
        time.sleep(0.1)
    st = c.get("/api/voice/tune").json()
    assert st.get("done") and st["step"] == 3 and st["best"]["exaggeration"] == 0.65, st
    assert c.get("/api/voice/status").json()["tuned"]["human_ref48.wav"]["cfg_weight"] == 0.3
    pid = c.post("/api/projects", json={"name": "v"}).json()["id"]
    c.post(f"/api/projects/{pid}/voice", json={"text": "## A\nHello there friend.\n", "voice": "human_ref48.wav",
                                               "approve": "once"})
    spec = c.fake.specs[-1]
    assert (spec["exaggeration"], spec["cfg_weight"], spec["temperature"]) == (0.65, 0.3, 0.85)
    c.post(f"/api/projects/{pid}/voice", json={"text": "## A\nHello there friend.\n", "voice": "human_ref48.wav",
                                               "exaggeration": 0.4, "approve": "once"})
    assert c.fake.specs[-1]["exaggeration"] == 0.4                          # what the user typed wins
