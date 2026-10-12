"""Generated hero assets (owner, 2026-10-12): a stand-in image server, the project index the Fusion kit reads,
reuse by prompt, other takes, the no-people/no-logo rule, the module's folder on a drive with room, the
per-draft OK for the GPU job, and the tool offered only once the module is installed."""
import json
from pathlib import Path

import httpx
import pytest

from app.agent import policy
from app.agent.runs import Run, Runs
from app.assets import AssetError, Assets, ImageService, style_prefix
from app.installer import IMAGE_MIN_FREE_GB, image_home
from app.storage import Store

CALLS: list[dict] = []


def image_server(request: httpx.Request) -> httpx.Response:
    """Shaped like imagegen/server.py: writes the PNG where it was asked and answers its size."""
    if request.url.path == "/api/meta":
        return httpx.Response(200, json={"loaded": True, "model": "stand-in"})
    body = json.loads(request.content)
    CALLS.append(body)
    Path(body["out"]).parent.mkdir(parents=True, exist_ok=True)
    Path(body["out"]).write_bytes(b"\x89PNG stand-in")
    return httpx.Response(200, json={"file": body["out"], "w": 812, "h": 1004, "seconds": 9.4})


@pytest.fixture
def memory_keyring():
    import keyring
    from app.security.memkeyring import MemoryKeyring
    prev = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    yield kr
    keyring.set_keyring(prev)


@pytest.fixture
def svc(tmp_path):
    CALLS.clear()
    store = Store(tmp_path / "data")
    pid = store.create_project("A")["id"]
    image = ImageService(transport=httpx.MockTransport(image_server))
    image.configure(Path("python.exe"), Path("server.py"), {}, None)
    return Assets(store, image), pid, store


ART = {"concept": "Vault ledger, brass and parchment", "palette": {"bg": "#0E1A16", "accent": "#E8B33A"}}


def test_an_asset_is_made_once_indexed_for_the_kit_and_reused(svc):
    assets, pid, store = svc
    a = assets.generate(pid, "vault-door", "a heavy round bank vault door, half open", ART)
    assert a["file"] == "assets/vault-door.png" and (a["w"], a["h"]) == (812, 1004) and not a["reused"]
    idx = json.loads((store.files_dir(pid) / "assets" / "index.json").read_text(encoding="utf-8"))
    assert idx["vault-door"]["file"] == "assets/vault-door.png" and idx["vault-door"]["h"] == 1004
    prompt = CALLS[0]["prompt"]
    assert "vault door" in prompt and "#E8B33A" in prompt and "no text" in prompt and "no people" in prompt
    again = assets.generate(pid, "vault-door", "a heavy round bank vault door, half open", ART)
    assert again["reused"] and len(CALLS) == 1                                  # same prompt: no GPU work
    other = assets.generate(pid, "vault-door", "a heavy round bank vault door, half open", ART, variant=1)
    assert not other["reused"] and len(CALLS) == 2 and CALLS[1]["seed"] != CALLS[0]["seed"]


@pytest.mark.parametrize("name, prompt, why", [
    ("Vault Door", "a door", "lower-case"),
    ("logo", "the Federal Reserve logo on a wall", "no logos"),
    ("face", "portrait of the Treasury Secretary", "no logos"),
    ("blank", "  ", "say what"),
])
def test_names_and_the_no_people_no_logo_rule(svc, name, prompt, why):
    assets, pid, _ = svc
    with pytest.raises(AssetError, match=why):
        assets.generate(pid, name, prompt)
    assert not CALLS


def test_not_installed_says_where_to_install():
    s = ImageService("http://127.0.0.1:9")          # not the real port: a running image server must not matter
    with pytest.raises(AssetError, match="isn't installed yet"):
        s.ensure_running()


def test_the_module_lives_on_a_drive_with_room(tmp_path):
    data = Path("C:/Users/x/AppData/Local/OneProduction")
    full = [{"drive": "C:", "free_gb": 10.9}, {"drive": "D:", "free_gb": 168.8}]
    assert image_home(data, None, full) == Path("D:\\") / "OneProduction" / "imagegen"     # the studio PC on 2026-10-12
    roomy = [{"drive": "C:", "free_gb": IMAGE_MIN_FREE_GB + 5}, {"drive": "D:", "free_gb": 500}]
    assert image_home(data, None, roomy) == data / "modules" / "imagegen"
    assert image_home(data, "E:\\Models\\imagegen", full) == Path("E:\\Models\\imagegen")


def test_style_prefix_carries_the_episode_look():
    s = style_prefix(ART)
    assert "#0E1A16" in s and "Vault ledger" in s and style_prefix(None).startswith("matte stylised 3D render")


def test_hugging_face_token_is_checked_for_the_flux_terms(memory_keyring):
    """First real install (2026-10-12): FLUX.1-schnell's repo is gated - 401 until the owner accepts its terms."""
    from app.assets import hf_token, set_hf_token
    accepted = {"v": False}

    def hf(request):
        if request.headers.get("Authorization") != "Bearer hf_GoodToken0123456789abcdef":
            return httpx.Response(401)
        if request.url.path == "/api/whoami-v2":
            return httpx.Response(200, json={"name": "owner"})
        return httpx.Response(200 if accepted["v"] else 403)
    t = httpx.MockTransport(hf)
    with pytest.raises(AssetError, match="starts with hf_"):
        set_hf_token("sk-not-a-token", t)
    with pytest.raises(AssetError, match="refused the token"):
        set_hf_token("hf_WrongToken0123456789abcdef", t)
    with pytest.raises(AssetError, match="Agree and access repository"):
        set_hf_token("hf_GoodToken0123456789abcdef", t)
    assert hf_token() is None
    accepted["v"] = True
    assert set_hf_token(" hf_GoodToken0123456789abcdef ", t) == {"hf_token": True, "hf_user": "owner"}
    assert hf_token() == "hf_GoodToken0123456789abcdef"
    from app.installer import ImageRecipe
    r = ImageRecipe(Path("D:/x/imagegen"), Path("imagegen"), {"vram_gb": 16, "blackwell": True})
    assert r.env()["HF_TOKEN"] == "hf_GoodToken0123456789abcdef"                # for the download only
    assert "HF_TOKEN" not in r.run_env() and r.run_env()["HF_HUB_OFFLINE"] == "1"


def test_the_gpu_job_asks_unless_the_draft_ok_covers_it(tmp_path):
    import asyncio
    assert policy.decide("mcp__studio__asset_generate", {}, tmp_path)[0] == "gate"
    assert policy.HEAVY["asset_generate"] == "image_assets"
    runs = Runs()
    run = Run("p", tmp_path)
    run.approved_jobs = {"image_assets"}
    assert asyncio.run(runs.permit(run, "mcp__studio__asset_generate", {"name": "x"},
                                   gate=lambda **k: {"label": "Generated assets", "uses": "GPU"})) == (True, "")
