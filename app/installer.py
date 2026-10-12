"""Installs what a module needs (plan R13 step 4) - inside One Production's data folder,
never system-wide, and only the items the user approved one by one (R12).

The voice recipe is the one proven on this studio's own PC (vo-studio/bootstrap.py):
Python 3.11 (a venv from an existing 3.11/3.12, else the official embeddable build),
ffmpeg essentials (has rubberband), PyTorch 2.8.0 - CUDA 12.8 for RTX 50-series (sm_120
has no kernels in older builds), CUDA 12.6 for older NVIDIA cards, CPU otherwise - then
chatterbox-tts with --no-deps so its torch pin can't downgrade PyTorch, and the models.

Rules learned the hard way (bootstrap.py):
  - every item is checked AFTER it runs, not just before; a download that 403s or an
    extract that nests a folder must not count as installed
  - downloads resume (HTTP Range on <file>.part) and are checked against sha256 when known
  - the module is "ready" only when the final check passed and wrote installed.json; a GPU
    install is proven with a real fp16 matmul on the device - torch.cuda.is_available()
    says True even when the kernels for the card are missing
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PY_URL = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
PY_SHA256 = "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"
PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
TORCH = ["torch==2.8.0", "torchaudio==2.8.0"]
TORCH_INDEX = {"cu128": "https://download.pytorch.org/whl/cu128",
               "cu126": "https://download.pytorch.org/whl/cu126",
               "cpu": "https://download.pytorch.org/whl/cpu"}
TORCH_MB = {"cu128": 3400, "cu126": 2900, "cpu": 260}
# chatterbox-tts's own dependencies minus torch/torchaudio (installed above) and gradio (its demo UI)
CHATTERBOX_DEPS = ["numpy>=1.24.0,<2.0.0", "librosa==0.11.0", "s3tokenizer", "transformers==5.2.0",
                   "diffusers==0.29.0", "resemble-perth>=1.0.0", "conformer==0.3.2", "safetensors==0.5.3",
                   "spacy-pkuseg", "pykakasi==2.3.0", "omegaconf"]
ENGINE_DEPS = ["faster-whisper", "soundfile", "fastapi", "uvicorn", "python-multipart", "huggingface_hub"]
MODELS = {"ResembleAI/chatterbox": ["ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors",
                                    "tokenizer.json", "conds.pt"],
          "deepdml/faster-whisper-large-v3-turbo-ct2": None}       # None = the whole repo
MODELS_MB = 4800
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Cancelled(Exception):
    pass


class InstallError(Exception):
    pass


@dataclass
class Item:
    id: str
    label: str
    why: str
    size_mb: float
    source: str
    run: Callable[["Ctx"], None]
    check: Callable[[], bool]
    status: str = "pending"          # present | pending | waiting | running | done | failed | cancelled | skipped
    progress: float = 0.0
    detail: str = ""
    error: str | None = None

    def public(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "label", "why", "size_mb", "source", "status",
                                              "progress", "detail", "error")}


@dataclass
class Ctx:
    item: Item
    stop: threading.Event
    log: list = field(default_factory=list)

    def progress(self, frac: float, detail: str = "") -> None:
        self.item.progress = max(0.0, min(1.0, frac))
        if detail:
            self.item.detail = detail

    def cancelled(self) -> bool:
        return self.stop.is_set()

    def check(self) -> None:
        if self.stop.is_set():
            raise Cancelled()


# ---------------------------------------------------------------- building blocks
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, ctx: Ctx, sha256: str | None = None, label: str = "",
             opener=urllib.request.urlopen) -> Path:
    """Resumable: bytes already in <dest>.part are kept and only the rest is fetched."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "OneProduction-setup"}
    if have:
        headers["Range"] = f"bytes={have}-"
    try:
        resp = opener(urllib.request.Request(url, headers=headers), timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:                 # nothing left to fetch: the .part is complete
            resp = None
        else:
            raise InstallError(f"{label or url}: the server said {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise InstallError(f"{label or url}: no connection ({getattr(e, 'reason', e)})") from e
    if resp is not None:
        with resp:
            status = getattr(resp, "status", 200)
            if have and status != 206:             # server ignored the range: start over
                have = 0
            total = have + int(resp.headers.get("Content-Length") or 0)
            got = have
            with part.open("ab" if have else "wb") as fh:
                while True:
                    ctx.check()
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    fh.write(chunk)
                    got += len(chunk)
                    if total:
                        ctx.progress(got / total, f"{label} - {got / 1e6:.0f} of {total / 1e6:.0f} MB")
    if sha256 and sha256_of(part).lower() != sha256.lower():
        part.unlink(missing_ok=True)
        raise InstallError(f"{label or url}: the download is damaged (checksum mismatch) - try again")
    part.replace(dest)
    return dest


def unzip_flat(zip_path: Path, dest: Path) -> None:
    """Extract; if everything sits in one wrapper folder, lift it out (bootstrap.py lesson)."""
    tmp = dest.with_name(dest.name + "_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            target = (tmp / n).resolve()
            if tmp.resolve() not in target.parents and target != tmp.resolve():
                raise InstallError(f"unsafe path in {zip_path.name}: {n}")
        z.extractall(tmp)
    inner = list(tmp.iterdir())
    src = inner[0] if len(inner) == 1 and inner[0].is_dir() else tmp
    shutil.rmtree(dest, ignore_errors=True)
    if dest.exists():
        shutil.rmtree(tmp, ignore_errors=True)
        raise InstallError(f"could not replace {dest} - close anything using it and try again")
    shutil.move(str(src), str(dest))
    shutil.rmtree(tmp, ignore_errors=True)


def run_cmd(argv: list[str], ctx: Ctx, env: dict | None = None, label: str = "", expect: int = 40) -> str:
    """A command whose output streams into the item's detail; Cancel kills it."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace", env={**os.environ, **(env or {})}, creationflags=NO_WINDOW)
    lines: list[str] = []
    seen = 0

    def pump():
        nonlocal seen
        for line in proc.stdout:
            line = line.rstrip()
            lines.append(line)
            if line.startswith(("Collecting", "Downloading", "Installing", "STEP")):
                seen += 1
                ctx.progress(min(0.95, seen / expect), f"{label} - {line[:80]}")
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    while proc.poll() is None:
        if ctx.cancelled():
            proc.kill()
            proc.wait()
            raise Cancelled()
        time.sleep(0.2)
    t.join(5)
    if proc.returncode != 0:
        tail = "\n".join(lines[-6:])
        raise InstallError(f"{label} failed (exit {proc.returncode}). Press Retry - it resumes.\n{tail}")
    return "\n".join(lines)


def find_python() -> str | None:
    """A Python 3.11/3.12 already on this PC (the voice stack has no 3.13 wheels)."""
    for name in ("python3.12", "python3.11", "python", "python3", "py"):
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            out = subprocess.run([exe, "-c", "import sys;print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
                                 capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW)
            if out.stdout.strip() in ("3.11", "3.12"):
                return exe
        except (OSError, subprocess.SubprocessError):
            continue
    return None


# ---------------------------------------------------------------- the voice module
class VoiceRecipe:
    """Everything the voice module needs, under data_dir/modules."""

    def __init__(self, data_dir: Path, engine_dir: Path, mode: str, gpu: dict | None,
                 opener=urllib.request.urlopen, system_python: Callable[[], str | None] = find_python):
        self.m = data_dir / "modules"
        self.root = self.m / "voice"
        self.engine_dir = engine_dir
        self.mode = mode                                  # "gpu" | "cpu"
        self.gpu = gpu
        self.opener = opener
        self.system_python = system_python
        self.torch_kind = torch_kind(mode, gpu)

    # where things land
    @property
    def python(self) -> Path:
        venv = self.root / "python" / "Scripts" / "python.exe"
        flat = self.root / "python" / "python.exe"
        return venv if venv.exists() else flat

    @property
    def hf_home(self) -> Path:
        return self.root / "hf"

    @property
    def marker(self) -> Path:
        return self.root / "installed.json"

    def ffmpeg(self) -> str | None:
        local = self.m / "ffmpeg" / "bin" / "ffmpeg.exe"
        if local.exists():
            return str(local)
        exe = shutil.which("ffmpeg")
        if exe and _has_rubberband(exe):
            return exe
        return None

    def env(self) -> dict[str, str]:
        ff = self.ffmpeg()
        env = {"HF_HOME": str(self.hf_home)}
        if ff:
            env["VOAPP_FFMPEG"] = ff
            probe = Path(ff).with_name("ffprobe.exe")
            if probe.exists():
                env["VOAPP_FFPROBE"] = str(probe)
        return env

    def _imports(self, *mods: str) -> bool:
        if not self.python.exists():
            return False
        try:
            r = subprocess.run([str(self.python), "-c", "import " + ", ".join(mods)], capture_output=True,
                               timeout=300, creationflags=NO_WINDOW)
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _models_present(self) -> bool:
        hub = self.hf_home / "hub"
        for repo, files in MODELS.items():
            snaps = list((hub / ("models--" + repo.replace("/", "--")) / "snapshots").glob("*"))
            if not snaps:
                return False
            if files and not all(any((s / f).exists() for s in snaps) for f in files):
                return False
        return True

    # the items, in order
    def items(self) -> list[Item]:
        idx = self.torch_kind
        return [
            Item("ffmpeg", "ffmpeg (audio tool)", "Joins, masters and converts the audio. The 'essentials' "
                 "build includes the rubberband pitch filter.", 115, "gyan.dev", self._ffmpeg,
                 lambda: self.ffmpeg() is not None),
            Item("python", "Python for the voice engine", "A private Python 3.11 just for the voice engine, "
                 "inside the data folder - nothing is installed on the PC itself.", 30, "python.org / pypa.io",
                 self._python, lambda: self.python.exists()),
            Item("torch", f"PyTorch ({'graphics card, CUDA ' + idx[2:4] + '.' + idx[4:] if idx != 'cpu' else 'processor only'})",
                 "The maths library the voice model runs on." + (
                     " RTX 50-series cards need this CUDA 12.8 build." if idx == "cu128" else ""),
                 TORCH_MB[idx], "download.pytorch.org", self._torch, lambda: self._imports("torch", "torchaudio")),
            Item("engine", "Voice engine libraries", "Chatterbox (the voice cloner), faster-whisper (the "
                 "read-check) and their helpers.", 650, "pypi.org", self._engine,
                 lambda: self._imports("chatterbox", "faster_whisper", "soundfile", "librosa", "fastapi")),
            Item("models", "Voice and speech-recognition models", "Chatterbox's voice model and the Whisper "
                 "model that checks every section was read right.", MODELS_MB, "huggingface.co",
                 self._models, self._models_present),
        ]

    def _ffmpeg(self, ctx: Ctx) -> None:
        zp = self.m / "ffmpeg.zip"
        sha = None
        try:
            with self.opener(urllib.request.Request(FFMPEG_URL + ".sha256",
                                                    headers={"User-Agent": "OneProduction-setup"}), timeout=30) as r:
                sha = r.read().decode().split()[0]
        except Exception:                                  # noqa: BLE001 - checked by running it instead
            pass
        download(FFMPEG_URL, zp, ctx, sha, "ffmpeg", opener=self.opener)
        unzip_flat(zp, self.m / "ffmpeg")
        zp.unlink(missing_ok=True)
        if not (self.m / "ffmpeg" / "bin" / "ffmpeg.exe").exists():
            raise InstallError("ffmpeg did not unpack where expected")

    def _python(self, ctx: Ctx) -> None:
        target = self.root / "python"
        existing = self.system_python()
        if existing:
            ctx.progress(0.3, f"using the Python already on this PC ({existing}) to make a private copy")
            run_cmd([existing, "-m", "venv", str(target)], ctx, label="Python")
        else:
            zp = self.root / "py.zip"
            download(PY_URL, zp, ctx, PY_SHA256, "Python", opener=self.opener)
            unzip_flat(zp, target)
            zp.unlink(missing_ok=True)
            for pth in target.glob("python*._pth"):          # the embeddable build switches site-packages off
                pth.write_text(pth.read_text(encoding="utf-8").replace("#import site", "import site"),
                               encoding="utf-8")
            gp = self.root / "get-pip.py"
            download(PIP_URL, gp, ctx, None, "pip", opener=self.opener)
            run_cmd([str(self.python), str(gp), "-q", "--no-warn-script-location"], ctx, label="pip")
            gp.unlink(missing_ok=True)
        if not self.python.exists():
            raise InstallError("the voice engine's Python did not install")

    def _pip(self, args: list[str], ctx: Ctx, label: str, expect: int = 40) -> None:
        run_cmd([str(self.python), "-m", "pip", "install", "--no-warn-script-location", "--disable-pip-version-check",
                 *args], ctx, label=label, expect=expect)

    def _torch(self, ctx: Ctx) -> None:
        self._pip([*TORCH, "--index-url", TORCH_INDEX[self.torch_kind]], ctx, "PyTorch", expect=12)

    def _engine(self, ctx: Ctx) -> None:
        self._pip([*CHATTERBOX_DEPS, *ENGINE_DEPS], ctx, "voice libraries", expect=70)
        self._pip(["--no-deps", "chatterbox-tts"], ctx, "Chatterbox", expect=3)

    def _models(self, ctx: Ctx) -> None:
        script = ("import sys\nfrom huggingface_hub import hf_hub_download, snapshot_download\n"
                  f"models = {MODELS!r}\n"
                  "for repo, files in models.items():\n"
                  "    print('STEP', repo, flush=True)\n"
                  "    if files:\n"
                  "        [hf_hub_download(repo, f) for f in files]\n"
                  "    else:\n"
                  "        snapshot_download(repo)\n")
        env = {"HF_HOME": str(self.hf_home), "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
        stop = threading.Event()

        def watch():                                    # progress = bytes in the model folder so far
            while not stop.wait(1.0):
                got = sum(f.stat().st_size for f in self.hf_home.rglob("*") if f.is_file()) if self.hf_home.exists() else 0
                ctx.progress(min(0.99, got / (MODELS_MB * 1e6)), f"models - {got / 1e9:.1f} of ~{MODELS_MB / 1e3:.1f} GB")
        w = threading.Thread(target=watch, daemon=True)
        w.start()
        try:
            run_cmd([str(self.python), "-c", script], ctx, env=env, label="models", expect=10_000)
        finally:
            stop.set()

    def verify(self, ctx: Ctx) -> dict:
        """The proof: everything imports, and on a GPU install the card really computes."""
        code = ("import json, torch, torchaudio, chatterbox, faster_whisper, soundfile, librosa, fastapi\n"
                "out = {'torch': torch.__version__, 'cuda': torch.cuda.is_available()}\n"
                "if out['cuda']:\n"
                "    a = torch.randn(256, 256, device='cuda', dtype=torch.float16)\n"
                "    (a @ a).sum().item()\n"
                "    torch.cuda.synchronize()\n"
                "    out['device'] = torch.cuda.get_device_name(0)\n"
                "print('RESULT ' + json.dumps(out))\n")
        text = run_cmd([str(self.python), "-c", code], ctx, env=self.env(), label="final check", expect=3)
        line = next((l for l in reversed(text.splitlines()) if l.startswith("RESULT ")), None)
        if not line:
            raise InstallError("the final check didn't report back")
        res = json.loads(line[7:])
        if self.mode == "gpu" and not res.get("cuda"):
            raise InstallError("PyTorch can't use the graphics card. Update the NVIDIA driver, or choose "
                               "'Processor only' for the voice module.")
        if not self._models_present():
            raise InstallError("the models are not all there - press Retry")
        info = {**res, "mode": self.mode, "torch_build": self.torch_kind, "verified_at": time.time(),
                "python": str(self.python)}
        self.marker.write_text(json.dumps(info, indent=1), encoding="utf-8")
        return info

    def ready(self) -> bool:
        """Cheap check, run often: the marker plus the files it stands for still being there."""
        return self.marker.exists() and self.python.exists() and self._models_present() and self.ffmpeg() is not None


# ---------------------------------------------------------------- the image assets module (2026-10-12)
IMAGE_MODELS = {"black-forest-labs/FLUX.1-schnell": ["model_index.json", "scheduler/*", "text_encoder/*", "text_encoder_2/*",
                                                     "tokenizer/*", "tokenizer_2/*", "vae/*", "transformer/config.json"],
                "city96/FLUX.1-schnell-gguf": ["flux1-schnell-Q5_K_S.gguf"],
                "ZhengPeng7/BiRefNet": None}
IMAGE_MODELS_MB = 19_500            # T5-XXL ~9.5 GB (loaded 4-bit), GGUF transformer ~8.3 GB, BiRefNet ~0.9 GB, rest ~0.5 GB
IMAGE_LIBS_MB = 900
IMAGE_MIN_FREE_GB = 30


def image_home(data_dir: Path, chosen: str | None, drives: list[dict]) -> Path:
    """Where the image module lives: the owner's choice, else the data folder if its drive has room, else the
    fixed drive with the most free space (the studio PC's C: had 11 GB free - 2026-10-12)."""
    if chosen:
        return Path(chosen)
    data_drive = str(data_dir.drive or data_dir.anchor).upper().rstrip("\\")
    best = max((d for d in drives if d.get("free_gb") is not None), key=lambda d: d["free_gb"], default=None)
    here = next((d for d in drives if str(d.get("drive", "")).upper() == data_drive), None)
    if (here and here.get("free_gb", 0) >= IMAGE_MIN_FREE_GB) or not best:
        return data_dir / "modules" / "imagegen"
    return Path(best["drive"] + "\\") / "OneProduction" / "imagegen"


class ImageRecipe(VoiceRecipe):
    """FLUX.1-schnell (slimmed) + BiRefNet in a private Python under `root` (owner's choice, 2026-10-12)."""

    def __init__(self, root: Path, engine_dir: Path, gpu: dict | None, opener=urllib.request.urlopen,
                 system_python: Callable[[], str | None] = find_python):
        self.m = root.parent
        self.root = root
        self.engine_dir = engine_dir
        self.mode = "gpu"
        self.gpu = gpu
        self.opener = opener
        self.system_python = system_python
        self.torch_kind = torch_kind("gpu", gpu)

    def env(self) -> dict[str, str]:
        env = {"HF_HOME": str(self.hf_home), "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
        from .assets import hf_token                      # FLUX's repo is gated: the owner's own read token
        token = hf_token()
        if token:
            env["HF_TOKEN"] = token
        return env

    def run_env(self) -> dict[str, str]:
        """For the server after the install: everything is local, so no network and no token are needed."""
        return {"HF_HOME": str(self.hf_home), "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}

    def _models_present(self) -> bool:
        hub = self.hf_home / "hub"
        need = {"black-forest-labs/FLUX.1-schnell": ["model_index.json", "text_encoder_2", "vae", "tokenizer_2"],
                "city96/FLUX.1-schnell-gguf": ["flux1-schnell-Q5_K_S.gguf"], "ZhengPeng7/BiRefNet": ["config.json"]}
        for repo, files in need.items():
            snaps = list((hub / ("models--" + repo.replace("/", "--")) / "snapshots").glob("*"))
            if not snaps or not all(any((s / f).exists() for s in snaps) for f in files):
                return False
        return True

    def items(self) -> list[Item]:
        idx = self.torch_kind
        return [
            Item("python", "Python for the image generator", "A private Python just for image generation, in "
                 f"{self.root} - nothing is installed on the PC itself.", 30, "python.org / pypa.io",
                 self._python, lambda: self.python.exists()),
            Item("torch", "PyTorch (graphics card, CUDA 12.8)" if idx == "cu128" else f"PyTorch ({idx})",
                 "The maths library FLUX runs on." + (" RTX 50-series cards need this CUDA 12.8 build." if idx == "cu128" else ""),
                 TORCH_MB.get(idx, 3400), "download.pytorch.org", self._torch,
                 lambda: self._imports("torch", "torchvision")),
            Item("libs", "Image generation libraries", "diffusers (FLUX), transformers, bitsandbytes (4-bit text "
                 "encoder), GGUF (5-bit FLUX), timm/kornia (the cut-out model).", IMAGE_LIBS_MB, "pypi.org", self._libs,
                 lambda: self._imports("diffusers", "transformers", "bitsandbytes", "gguf", "timm", "fastapi")),
            Item("models", "FLUX.1-schnell and the cut-out model", "FLUX.1-schnell (Apache-2.0, free for commercial "
                 "use): its text encoders, VAE and a 5-bit transformer; BiRefNet (MIT) cuts each object out. Needs your "
                 "free Hugging Face read token (FLUX's page asks you to accept its terms once).",
                 IMAGE_MODELS_MB, "huggingface.co", self._image_models, self._models_present),
        ]

    def _torch(self, ctx: Ctx) -> None:
        self._pip(["torch==2.8.0", "torchvision==0.23.0", "--index-url", TORCH_INDEX[self.torch_kind]], ctx, "PyTorch", expect=12)

    def _libs(self, ctx: Ctx) -> None:
        self._pip(["-r", str(self.engine_dir / "requirements.txt")], ctx, "image libraries", expect=60)

    def _image_models(self, ctx: Ctx) -> None:
        script = ("from huggingface_hub import hf_hub_download, snapshot_download\n"
                  f"models = {IMAGE_MODELS!r}\n"
                  "for repo, pats in models.items():\n"
                  "    print('STEP', repo, flush=True)\n"
                  "    if pats and len(pats) == 1 and '*' not in pats[0]:\n"
                  "        hf_hub_download(repo, pats[0])\n"
                  "    else:\n"
                  "        snapshot_download(repo, allow_patterns=pats)\n")
        stop = threading.Event()

        def watch():
            while not stop.wait(1.0):
                got = sum(f.stat().st_size for f in self.hf_home.rglob("*") if f.is_file()) if self.hf_home.exists() else 0
                ctx.progress(min(0.99, got / (IMAGE_MODELS_MB * 1e6)), f"models - {got / 1e9:.1f} of ~{IMAGE_MODELS_MB / 1e3:.1f} GB")
        w = threading.Thread(target=watch, daemon=True)
        w.start()
        try:
            run_cmd([str(self.python), "-c", script], ctx, env=self.env(), label="models", expect=10_000)
        finally:
            stop.set()

    def verify(self, ctx: Ctx) -> dict:
        """Imports, a real fp16 matmul AND a real 4-bit layer on the card - a library that installs but has no
        kernels for a Blackwell card must fail here, not in the middle of an episode."""
        code = ("import json, torch, diffusers, transformers, gguf, timm, fastapi, bitsandbytes as bnb\n"
                "out = {'torch': torch.__version__, 'cuda': torch.cuda.is_available(), 'diffusers': diffusers.__version__}\n"
                "a = torch.randn(256, 256, device='cuda', dtype=torch.float16); (a @ a).sum().item()\n"
                "lin = bnb.nn.Linear4bit(64, 64, compute_dtype=torch.bfloat16, quant_type='nf4').to('cuda')\n"
                "lin(torch.randn(2, 64, device='cuda', dtype=torch.bfloat16)).sum().item(); torch.cuda.synchronize()\n"
                "out['device'] = torch.cuda.get_device_name(0); out['bnb'] = bnb.__version__\n"
                "print('RESULT ' + json.dumps(out))\n")
        text = run_cmd([str(self.python), "-c", code], ctx, env=self.env(), label="final check", expect=3)
        line = next((l for l in reversed(text.splitlines()) if l.startswith("RESULT ")), None)
        if not line:
            raise InstallError("the final check didn't report back: " + text.strip()[-300:])
        res = json.loads(line[7:])
        if not self._models_present():
            raise InstallError("the models are not all there - press Retry")
        info = {**res, "torch_build": self.torch_kind, "verified_at": time.time(), "python": str(self.python),
                "root": str(self.root)}
        self.marker.write_text(json.dumps(info, indent=1), encoding="utf-8")
        return info

    def ready(self) -> bool:
        return self.marker.exists() and self.python.exists() and self._models_present()


def torch_kind(mode: str, gpu: dict | None) -> str:
    if mode != "gpu" or not gpu:
        return "cpu"
    major = int(str(gpu.get("compute") or "0").split(".")[0] or 0)
    if gpu.get("blackwell") or major >= 12:
        return "cu128"
    if gpu.get("compute") and float(gpu["compute"]) < 5.0:
        return "cpu"                                     # too old for current CUDA builds
    return "cu126"


def _has_rubberband(exe: str) -> bool:
    try:
        out = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20,
                             creationflags=NO_WINDOW).stdout
        return "rubberband" in out
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------- running a plan
class Installer:
    """One install at a time, in a background thread; the UI polls state()."""

    def __init__(self):
        self.items: list[Item] = []
        self.module: str | None = None
        self.thread: threading.Thread | None = None
        self.stop = threading.Event()
        self.finished: dict | None = None
        self.lock = threading.Lock()

    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def plan(self, module: str, items: list[Item]) -> list[dict]:
        if self.running():
            return [i.public() for i in self.items]
        for i in items:
            i.status = "present" if i.check() else "pending"
        self.items, self.module, self.finished = items, module, None
        return [i.public() for i in items]

    def start(self, approved: list[str], verify: Callable[[Ctx], dict] | None = None,
              on_done: Callable[[dict], None] | None = None) -> None:
        with self.lock:
            if self.running():
                raise InstallError("an install is already running")
            todo = [i for i in self.items if i.status != "present" and i.status != "done"]
            missing = [i.label for i in todo if i.id not in approved]
            if missing:
                raise InstallError("each item needs your OK first: " + ", ".join(missing))
            self.stop.clear()
            self.finished = None
            for i in todo:
                i.status, i.error, i.progress = "waiting", None, 0.0
            self.thread = threading.Thread(target=self._run, args=(todo, verify, on_done), daemon=True)
            self.thread.start()

    def _run(self, todo: list[Item], verify, on_done) -> None:
        result = {"ok": False}
        try:
            for item in todo:
                ctx = Ctx(item, self.stop)
                item.status, item.detail = "running", "starting"
                try:
                    item.run(ctx)
                    if not item.check():                       # proof after the step, not just before
                        raise InstallError(f"{item.label} ran but isn't there afterwards - press Retry")
                    item.status, item.progress, item.detail = "done", 1.0, "done"
                except Cancelled:
                    item.status, item.detail = "cancelled", "cancelled - press Install to resume"
                    for rest in todo[todo.index(item) + 1:]:
                        rest.status = "pending"
                    result = {"ok": False, "cancelled": True}
                    return
                except Exception as e:                         # noqa: BLE001 - shown on the item
                    item.status, item.error = "failed", str(e)[:600]
                    for rest in todo[todo.index(item) + 1:]:
                        rest.status = "pending"
                    result = {"ok": False, "failed": item.id}
                    return
            if verify:
                check = Item("verify", "Final check", "", 0, "", lambda c: None, lambda: True)
                self.items.append(check)
                check.status = "running"
                try:
                    result = {"ok": True, "info": verify(Ctx(check, self.stop))}
                    check.status, check.progress, check.detail = "done", 1.0, "all working"
                except Exception as e:                         # noqa: BLE001
                    check.status, check.error = "failed", str(e)[:600]
                    result = {"ok": False, "failed": "verify"}
            else:
                result = {"ok": True}
        finally:
            self.finished = result
            if on_done:
                try:
                    on_done(result)
                except Exception:                              # noqa: BLE001
                    pass

    def cancel(self) -> bool:
        if not self.running():
            return False
        self.stop.set()
        return True

    def state(self) -> dict:
        return {"module": self.module, "running": self.running(), "finished": self.finished,
                "items": [i.public() for i in self.items]}
