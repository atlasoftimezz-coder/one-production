"""The installer (plan R13 step 4): resumable checked downloads, one OK per item, proof after
every step, and a module that is never 'ready' half-installed."""
import io
import threading
import zipfile
from pathlib import Path

import pytest

from app import installer as I

from .conftest import GTX1060, RTX3060, RTX5060TI, sha


def ctx_for(stop_at=None):
    item = I.Item("x", "x", "", 1, "", lambda c: None, lambda: True)
    stop = threading.Event()
    c = I.Ctx(item, stop)
    if stop_at is not None:
        orig = c.progress

        def progress(frac, detail=""):
            orig(frac, detail)
            if frac >= stop_at:
                stop.set()
        c.progress = progress
    return c


def test_cancelled_download_resumes_where_it_stopped(server, tmp_path):
    body = bytes(range(256)) * 8000                      # ~2 MB
    server.files["/big.zip"] = body
    dest = tmp_path / "big.zip"
    with pytest.raises(I.Cancelled):
        I.download(server.base + "/big.zip", dest, ctx_for(stop_at=0.3), sha(body), "big")
    part = tmp_path / "big.zip.part"
    kept = part.stat().st_size
    assert 0 < kept < len(body) and not dest.exists()
    I.download(server.base + "/big.zip", dest, ctx_for(), sha(body), "big")
    assert dest.read_bytes() == body
    assert server.log[-1] == ("/big.zip", f"bytes={kept}-")        # asked only for the rest


def test_damaged_download_is_refused_and_thrown_away(server, tmp_path):
    server.files["/f.zip"] = b"genuine bytes"
    with pytest.raises(I.InstallError, match="damaged"):
        I.download(server.base + "/f.zip", tmp_path / "f.zip", ctx_for(), sha(b"other bytes"), "f")
    assert not (tmp_path / "f.zip").exists() and not (tmp_path / "f.zip.part").exists()


def test_complete_part_file_is_finished_without_downloading_again(server, tmp_path):
    body = b"x" * 5000
    server.files["/done.bin"] = body
    (tmp_path / "done.bin.part").write_bytes(body)
    I.download(server.base + "/done.bin", tmp_path / "done.bin", ctx_for(), sha(body), "done")
    assert (tmp_path / "done.bin").read_bytes() == body


def test_no_connection_is_explained(tmp_path):
    with pytest.raises(I.InstallError, match="no connection"):
        I.download("http://127.0.0.1:9/nothing", tmp_path / "n", ctx_for(), None, "thing")


def zip_bytes(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, b"data")
    return buf.getvalue()


def test_unzip_lifts_the_wrapper_folder_and_refuses_escapes(tmp_path):
    zp = tmp_path / "ff.zip"
    zp.write_bytes(zip_bytes(["ffmpeg-7.1-essentials/bin/ffmpeg.exe", "ffmpeg-7.1-essentials/bin/ffprobe.exe"]))
    I.unzip_flat(zp, tmp_path / "ffmpeg")
    assert (tmp_path / "ffmpeg" / "bin" / "ffmpeg.exe").exists()          # not ffmpeg/ffmpeg-7.1-.../bin
    zp.write_bytes(zip_bytes(["../../evil.exe"]))
    with pytest.raises(I.InstallError, match="unsafe"):
        I.unzip_flat(zp, tmp_path / "other")
    assert not (tmp_path.parent / "evil.exe").exists()


def test_pytorch_build_matches_the_graphics_card():
    assert I.torch_kind("gpu", RTX5060TI) == "cu128"          # sm_120 has no kernels in older builds
    assert I.torch_kind("gpu", RTX3060) == "cu126"
    assert I.torch_kind("gpu", GTX1060) == "cu126"
    assert I.torch_kind("gpu", None) == "cpu"
    assert I.torch_kind("cpu", RTX5060TI) == "cpu"
    assert I.torch_kind("gpu", {"name": "old", "vram_gb": 8, "compute": "3.5"}) == "cpu"


# ---------------------------------------------------------------- running a plan
def plan_items(log, fail_once=None, lie=None):
    done = set()

    def mk(i):
        def run(ctx):
            log.append(i)
            if fail_once == i and i not in done:
                done.add(i)
                raise I.InstallError(f"{i} broke")
            if lie != i:
                done.add(i + "-ok")
        return I.Item(i, i.title(), "", 10, "test", run, lambda: i + "-ok" in done)
    return [mk(i) for i in ("ffmpeg", "python", "torch")], done


def wait(inst):
    inst.thread.join(10)


def test_every_item_needs_its_own_ok():
    inst, log = I.Installer(), []
    items, _ = plan_items(log)
    inst.plan("voice", items)
    with pytest.raises(I.InstallError, match="needs your OK"):
        inst.start(["ffmpeg", "python"])                   # torch not approved
    assert log == []


def test_present_items_are_skipped_and_failure_stops_the_rest_then_retry_resumes():
    inst, log = I.Installer(), []
    items, done = plan_items(log, fail_once="python")
    done.add("ffmpeg-ok")                                  # already on this PC
    inst.plan("voice", items)
    assert [i["status"] for i in inst.state()["items"]] == ["present", "pending", "pending"]
    inst.start(["python", "torch"])
    wait(inst)
    st = {i["id"]: i for i in inst.state()["items"]}
    assert st["python"]["status"] == "failed" and "broke" in st["python"]["error"]
    assert st["torch"]["status"] == "pending" and log == ["python"]
    inst.start(["python", "torch"])                        # Retry: only what's left
    wait(inst)
    assert log == ["python", "python", "torch"] and inst.state()["finished"]["ok"]


def test_a_step_that_claims_success_but_left_nothing_fails():
    inst, log = I.Installer(), []
    items, _ = plan_items(log, lie="torch")
    inst.plan("voice", items)
    inst.start(["ffmpeg", "python", "torch"])
    wait(inst)
    st = {i["id"]: i for i in inst.state()["items"]}
    assert st["torch"]["status"] == "failed" and "isn't there afterwards" in st["torch"]["error"]
    assert inst.state()["finished"]["ok"] is False


def test_failed_final_check_means_not_installed():
    inst, log = I.Installer(), []
    items, _ = plan_items(log)
    inst.plan("voice", items)

    def verify(ctx):
        raise I.InstallError("PyTorch can't use the graphics card")
    inst.start(["ffmpeg", "python", "torch"], verify=verify)
    wait(inst)
    st = inst.state()
    assert st["finished"]["ok"] is False and st["items"][-1]["id"] == "verify"
    assert "graphics card" in st["items"][-1]["error"]


def test_cancel_stops_and_install_resumes():
    inst, log = I.Installer(), []
    gate = threading.Event()

    def slow(ctx):
        log.append("slow")
        while not gate.wait(0.05):
            ctx.check()
    proof = {"ok": False}
    items = [I.Item("models", "Models", "", 10, "t", slow, lambda: proof["ok"])]
    inst.plan("voice", items)
    inst.start(["models"])
    assert inst.cancel()
    wait(inst)
    assert inst.state()["items"][0]["status"] == "cancelled"
    gate.set()
    proof["ok"] = True
    inst.start(["models"])
    wait(inst)
    assert inst.state()["items"][0]["status"] == "done" and log == ["slow", "slow"]


# ---------------------------------------------------------------- the real voice recipe, commands faked
def recipe(tmp_path, gpu, mode="gpu", monkeypatch=None, calls=None):
    r = I.VoiceRecipe(tmp_path / "data", tmp_path / "engine", mode, gpu, system_python=lambda: "C:/Py312/python.exe")
    if monkeypatch is not None:
        def fake(argv, ctx, env=None, label="", expect=40):
            calls.append(argv)
            if argv[1:3] == ["-m", "venv"]:                     # what `python -m venv` leaves behind
                exe = Path(argv[3]) / "Scripts" / "python.exe"
                exe.parent.mkdir(parents=True, exist_ok=True)
                exe.write_bytes(b"")
            return ""
        monkeypatch.setattr(I, "run_cmd", fake)
    return r


def test_voice_recipe_uses_the_studio_proven_install(tmp_path, monkeypatch):
    calls = []
    r = recipe(tmp_path, RTX5060TI, calls=calls, monkeypatch=monkeypatch)
    items = {i.id: i for i in r.items()}
    assert list(items) == ["ffmpeg", "python", "torch", "engine", "models"]
    assert "CUDA 12.8" in items["torch"].label and items["torch"].size_mb == 3400
    items["python"].run(ctx_for())
    assert calls[-1][:3] == ["C:/Py312/python.exe", "-m", "venv"]       # a private copy, not the system one
    items["torch"].run(ctx_for())
    assert calls[-1][-4:] == ["torch==2.8.0", "torchaudio==2.8.0", "--index-url",
                              "https://download.pytorch.org/whl/cu128"]
    items["engine"].run(ctx_for())
    assert calls[-1][-2:] == ["--no-deps", "chatterbox-tts"]              # its torch pin can't downgrade PyTorch
    assert "faster-whisper" in calls[-2]


def test_processor_only_install_is_small(tmp_path):
    r = recipe(tmp_path, None, mode="cpu")
    torch = next(i for i in r.items() if i.id == "torch")
    assert "processor only" in torch.label and torch.size_mb < 500


def test_module_is_not_ready_until_verified_and_complete(tmp_path):
    r = recipe(tmp_path, RTX5060TI)
    py = r.root / "python" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    assert not r.ready()                                    # files but no proof
    r.marker.write_text("{}")
    assert not r.ready()                                    # proof, but the models are missing
    for repo, files in I.MODELS.items():
        snap = r.hf_home / "hub" / ("models--" + repo.replace("/", "--")) / "snapshots" / "abc"
        snap.mkdir(parents=True)
        for f in files or ["model.bin"]:
            (snap / f).write_bytes(b"w")
    ff = r.m / "ffmpeg" / "bin" / "ffmpeg.exe"
    ff.parent.mkdir(parents=True)
    ff.write_bytes(b"")
    assert r.ready()
    (snap / "model.bin").unlink() if (snap / "model.bin").exists() else None
    first = next(iter(I.MODELS))
    for f in I.MODELS[first]:
        next(r.hf_home.glob(f"hub/models--{first.replace('/', '--')}/snapshots/*/{f}")).unlink()
        break
    assert not r.ready()                                    # a model file went missing later
