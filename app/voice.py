"""Voiceover module (plan R5): an adapter to the separate local voiceover app (vo-app).

One Production never loads the voice model itself. It talks to vo-app's local HTTP API,
starting it in the background if needed, then turns the finished audio into subtitles and
puts mp3 + wav + srt into the project's Files. Where vo-app lives comes from settings
(voice_app_dir / voice_python), so on a PC without it the module just reports "not set up".
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import httpx

log = logging.getLogger("oneprod.voice")
POLL = 2.0
START_TIMEOUT = 90


class VoiceError(RuntimeError):
    pass


class VoiceService:
    def __init__(self, settings, transport: httpx.AsyncBaseTransport | None = None):
        self.app_dir = Path(settings.voice_app_dir) if settings.voice_app_dir else None
        self.python = Path(settings.voice_python) if settings.voice_python else None
        self.url = (settings.voice_app_url or "http://127.0.0.1:8720").rstrip("/")
        self.transport = transport
        self._proc: subprocess.Popen | None = None
        self.last_meta: dict | None = None          # voices/presets as last seen, for the chat

    # ---------------------------------------------------------------- setup
    def missing(self) -> list[str]:
        out = []
        if not self.app_dir or not (self.app_dir / "server.py").is_file():
            out.append("voice_app_dir: folder of the voiceover app (with server.py)")
        if not self.python or not self.python.is_file():
            out.append("voice_python: the Python that has the voice model installed")
        return out

    def _client(self, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport, timeout=timeout)

    async def running(self) -> bool:
        try:
            async with self._client(5) as c:
                return (await c.get(self.url + "/api/meta")).status_code == 200
        except httpx.HTTPError:
            return False

    async def ensure_running(self) -> None:
        if await self.running():
            return
        if self.missing():
            raise VoiceError("the voiceover app is not set up on this PC: " + "; ".join(self.missing()))
        port = self.url.rsplit(":", 1)[-1]
        log.info("starting the voiceover app on %s", self.url)
        self._proc = subprocess.Popen(
            [str(self.python), str(self.app_dir / "server.py"), "--port", port],
            cwd=str(self.app_dir), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            if await self.running():
                return
            if self._proc.poll() is not None:
                raise VoiceError("the voiceover app stopped while starting (exit code "
                                 f"{self._proc.returncode})")
            await asyncio.sleep(1)
        raise VoiceError("the voiceover app did not start within 90 seconds")

    async def meta(self) -> dict:
        async with self._client() as c:
            r = await c.get(self.url + "/api/meta")
            r.raise_for_status()
            self.last_meta = r.json()             # the chat assistant reads this copy
            return self.last_meta

    async def submit(self, spec: dict) -> str:
        async with self._client() as c:
            r = await c.post(self.url + "/api/jobs", json=spec)
            if r.status_code != 200:
                raise VoiceError(r.json().get("detail", f"HTTP {r.status_code}"))
            return r.json()["id"]

    async def job(self, jid: str) -> dict:
        async with self._client() as c:
            r = await c.get(f"{self.url}/api/jobs/{jid}")
            r.raise_for_status()
            return r.json()

    async def add_voice(self, filename: str, data: bytes) -> dict:
        """vo-app cleans the clip (high-pass, loudness, 48 kHz mono) and saves <name>_ref48.wav."""
        async with self._client(timeout=120) as c:
            r = await c.post(self.url + "/api/voices", files={"file": (filename, data)})
            if r.status_code != 200:
                raise VoiceError(r.json().get("detail", f"HTTP {r.status_code}"))
            return r.json()

    async def make_srt(self, audio: Path, script: Path, out: Path) -> None:
        """vo-app's make_srt.py: timing from speech recognition, text from the script."""
        proc = await asyncio.create_subprocess_exec(
            str(self.python), str(self.app_dir / "make_srt.py"), str(audio), str(script), str(out),
            cwd=str(self.app_dir), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONUTF8": "1"})
        _, err = await proc.communicate()
        if proc.returncode != 0 or not out.exists():
            raise VoiceError("subtitles failed: " + err.decode("utf-8", "replace").strip()[-300:])


class VoiceJobs:
    """Jobs started from the chat. State lives in memory while running and in
    data_dir/cache/voicejobs/<id>.json, so a finished job survives a restart."""

    def __init__(self, service: VoiceService, store, data_dir: Path):
        self.svc = service
        self.store = store
        self.dir = data_dir / "cache" / "voicejobs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self.tasks: set[asyncio.Task] = set()

    def for_project(self, pid: str) -> list[dict]:
        """Newest first, finished ones from disk plus any still running."""
        jobs = {}
        for f in self.dir.glob("*.json"):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if j.get("project") == pid:
                jobs[j["id"]] = j
        jobs.update({k: v for k, v in self.jobs.items() if v.get("project") == pid})
        return sorted(jobs.values(), key=lambda j: j.get("created", 0), reverse=True)

    def get(self, jid: str) -> dict | None:
        if jid in self.jobs:
            return self.jobs[jid]
        f = self.dir / f"{jid}.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() and jid.isalnum() else None

    def _save(self, job: dict) -> None:
        (self.dir / f"{job['id']}.json").write_text(json.dumps(job, indent=1), encoding="utf-8")

    def start(self, pid: str, script: str, spec: dict, source: str) -> dict:
        jid = secrets.token_hex(6)
        job = {"id": jid, "project": pid, "status": "starting", "step": "starting the voice engine",
               "name": spec["name"], "voice": spec["voice"], "preset": spec.get("preset"),
               "pace_mode": spec.get("pace_mode"), "target_wpm": spec.get("target_wpm"),
               "source": source, "created": time.time(), "sections": [], "files": [], "error": None,
               "result": None}
        self.jobs[jid] = job
        self._save(job)
        task = asyncio.get_running_loop().create_task(self._run(job, script, spec))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return job

    async def _run(self, job: dict, script: str, spec: dict) -> None:
        pid = job["project"]
        try:
            await self.svc.ensure_running()
            job.update(status="rendering", step="sending the script to the voice engine")
            remote = await self.svc.submit({**spec, "script": script})
            job["remote"] = remote
            while True:
                r = await self.svc.job(remote)
                job["step"] = r.get("message") or r.get("status")
                job["sections"] = r.get("sections") or []
                if r.get("status") == "failed":
                    raise VoiceError(r.get("error") or "the voice engine reported a failure")
                if r.get("status") == "done":
                    break
                await asyncio.sleep(POLL)
            res = r["result"]
            job.update(step="copying audio into the project")
            files = []
            for key in ("mp3", "wav"):
                src = Path(res[key])
                dst = self.store.unique_path(pid, f"{job['name']}{src.suffix}")
                shutil.copyfile(src, dst)
                files.append(dst.name)
            job.update(status="subtitles", step="making subtitles from the audio")
            script_file = self.dir / f"{job['id']}-script.md"
            script_file.write_text(script, encoding="utf-8")
            srt = self.store.unique_path(pid, f"{job['name']}.srt")
            wav = self.store.files_dir(pid) / files[1]
            await self.svc.make_srt(wav, script_file, srt)
            files.append(srt.name)
            job.update(status="done", step="done", files=files,
                       result={k: res.get(k) for k in ("seconds", "clock", "words", "wpm", "flagged")})
        except Exception as e:
            log.exception("voice job %s failed", job["id"])
            job.update(status="failed", step="failed", error=str(e)[:400] or e.__class__.__name__)
        finally:
            self._save(job)
            self.store.append_message(pid, "tool", "", tool="voiceover", job=job["id"],
                                      status=job["status"])
