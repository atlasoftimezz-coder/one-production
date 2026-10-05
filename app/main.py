"""HTTP application.

Public (no token): /health and /  - the page itself holds no secret.
Everything under /api/ needs the session token (see security/guard.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import secrets
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from .learning import Learning
from .learning_api import mount as mount_learning
from .knowledge import Knowledge
from .knowledge_api import mount as mount_knowledge
from .resolve import Resolve
from .resolve_api import mount as mount_resolve
from .gates_api import Gates, mount as mount_gates
from .editor import Editor
from .editor_api import mount as mount_editor
from .animation import Animation
from . import handoff
from .accounts import DEFAULT_SWITCH, DEFAULT_WARN, PROVIDERS, SAME_SERVICE_SWITCHING, SUBSCRIPTIONS, \
    AccountError, Accounts
from . import actions, system
from . import models as M
from . import diagnostics
from .config import save_setting, settings_file
from .migrate import migrate
from .update import Updater
from .installer import InstallError, Installer
from .modules import ENGINE_DIR, Modules
from .setup import SetupState, drives
from .backups import BackupError, Backups, Busy
from .profile import Profile, ProfileError, Throttled
from .agent.claude_agent import ClaudeAgent
from .agent.codex_agent import CodexAgent
from .agent.runs import Runs
from .agent.team import Team, TeamConfig
from .agent.scriptflow import ScriptConfig, ScriptRun
from . import competitors, faceless
from .agent.studio import INSTRUCTIONS as STUDIO_INSTRUCTIONS, Studio
from .chat import SYSTEM, build_prompt, ndjson, reply_events
from .config import Settings
from .providers.anthropic_api import AnthropicAPI
from .providers.base import Fail, RateLimit, Text, Usage
from .providers.claude_cli import ClaudeCLI
from .providers.codex_cli import CodexCLI
from .providers.demo import Demo
from .security.guard import GuardMiddleware
from .security.redact import redact, register_secret
from .scripts import SUPPORTED as SCRIPT_TYPES, read_script, stats as script_stats
from .storage import NotFound, Store
from .tools import Approvals, TOOLS
from .usage import RANGES, Ledger, range_start
from .accounts import CAP_FIELDS, switch_allowed
from .version import __version__
from .voice import VoiceTuner, VoiceError, VoiceJobs, VoiceService

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
UPLOAD_CHUNK = 1024 * 1024
MAX_CLIP = 50 * 1024 * 1024      # a 30 s wav is ~3-6 MB; anything far bigger is not a reference clip
DEMO_ACCOUNT = {"id": "demo", "provider": "demo", "label": "Demo (no account)", "enabled": True,
                "status": "ok", "model": None}
log = logging.getLogger("oneprod.app")


class NewProject(BaseModel):
    name: str = ""


class ChatIn(BaseModel):
    message: str
    account_id: str | None = None


class ContinueIn(BaseModel):
    to_account: str
    from_account: str
    handoff: str
    trigger: str = "manual"


class SwitchIn(BaseModel):
    account_id: str


class NewAccount(BaseModel):
    provider: str
    label: str = ""
    api_key: str | None = None
    model: str | None = None


class AccountPatch(BaseModel):
    label: str | None = None
    enabled: bool | None = None
    model: str | None = None
    effort: str | None = None                  # "auto" = the client's default
    warn_pct: int | None = None
    switch_pct: int | None = None
    budget_tokens_5h: int | None = -1          # -1 = leave unchanged, null = clear
    caps: dict | None = None                   # {cap_task_tokens, cap_day_tokens, cap_day_usd}; null value clears


class PartIn(BaseModel):
    title: str = ""
    brief: str = ""
    files: list[str] = []
    gpu: bool = False


class TeamIn(BaseModel):
    goal: str = ""
    config: dict | None = None                 # overrides of the saved team settings for this run
    tasks: list[PartIn] | None = None          # the user's own parts: the master then only reviews


class ScriptRunIn(BaseModel):
    topic: str
    format: str = "documentary"
    minutes: float = 10
    niche: str = ""
    notes: str = ""
    links_text: str = ""
    links_file: str | None = None            # a competitor list saved in the project (any format)


class ScriptSettingsIn(BaseModel):
    facelessos_dir: str | None = None
    rounds: int | None = None
    pass_score: int | None = None
    stall: int | None = None
    per_channel: int | None = None
    master_model: str | None = None
    master_effort: str | None = None
    worker_model: str | None = None
    worker_effort: str | None = None
    master_account: str | None = None


class TeamSettingsIn(BaseModel):
    master_account: str | None = None
    master_model: str | None = None
    master_effort: str | None = None
    worker_model: str | None = None
    worker_effort: str | None = None
    max_workers: int | None = None


class OrderIn(BaseModel):
    ids: list[str]


class ScriptIn(BaseModel):
    file: str | None = None          # a file already in the project
    text: str | None = None          # or pasted text


class SourceIn(BaseModel):
    source: str | None = None


class VoiceModeIn(BaseModel):
    mode: str


class InstallIn(BaseModel):
    approve: list[str] = []


class SetupIn(BaseModel):
    step: int | None = None
    done: bool | None = None


class FolderIn(BaseModel):
    path: str


class TuneIn(BaseModel):
    voice: str
    approve: str | None = None


class BackupIn(BaseModel):
    include_media: bool | None = None


class BackupSettingsIn(BaseModel):
    folder: str | None = None
    include_media: bool | None = None
    keep: int | None = None


class RestoreIn(BaseModel):
    project: str


class ProfileIn(BaseModel):
    name: str


class PinIn(BaseModel):
    current: str | None = None
    new: str | None = None


class UnlockIn(BaseModel):
    pin: str


class AgentAnswer(BaseModel):
    id: str
    answer: str


class VoiceIn(ScriptIn):
    voice: str
    name: str | None = None
    preset: str = "clone_exact"
    pace_mode: str = "natural"
    target_wpm: float | None = None
    grid_gap: float = 1.5
    exaggeration: float | None = None
    cfg_weight: float | None = None
    temperature: float | None = None
    approve: str | None = None       # None | "once" | "always"  (heavy-job gate, R12)
    from_action: float | None = None  # time of the assistant message whose Run card started this


AGENT_PROVIDERS = ("claude", "anthropic_api", "codex")


def create_app(settings: Settings, token: str | None = None, *, api_transport=None,
               login_console: bool | None = None, voice_transport=None, agent_client=None,
               codex_transport=None, backup_delay: float = 120, backup_tick: float = 3600,
               restart=None, gpu_probe=None, tune_script: Path | None = None, relaunch=None,
               updater: Updater | None = None, ytdlp=None) -> FastAPI:
    if login_console is None:
        login_console = settings.login_console
    token = token or secrets.token_urlsafe(32)
    register_secret(token)
    knowledge = Knowledge(settings.data_dir)
    learning = Learning(knowledge)
    store = Store(settings.data_dir)
    resolve = Resolve(store, settings, learning)
    accounts = Accounts(settings.data_dir)
    ledger = Ledger(settings.data_dir)
    approvals = Approvals(settings.data_dir)
    voice_svc = VoiceService(settings, voice_transport)
    voice_jobs = VoiceJobs(voice_svc, store, settings.data_dir)
    gates = Gates(store, settings.data_dir, voice_svc, voice_jobs)
    editor = Editor(store, settings.data_dir, gates)
    animation = Animation(store, settings.data_dir)
    runs = Runs()
    backups = Backups(settings.data_dir, store, redact)
    profile = Profile(settings.data_dir)
    modules = Modules(settings, settings.data_dir)
    installer = Installer()
    setup_state = SetupState(settings.data_dir, store, accounts)
    tuner = VoiceTuner(voice_svc, settings.data_dir, tune_script or ENGINE_DIR / "tune.py")
    gpu_cache: dict = {}
    updater = updater or Updater()
    migration = migrate(settings.data_dir)
    SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "getting-started"
    claude_agent = ClaudeAgent(settings.claude_cmd, settings.data_dir, client_factory=agent_client,
                               api_key_of=Accounts.api_key)
    codex_agent = CodexAgent(settings.codex_cmd, transport_factory=codex_transport)
    work = settings.data_dir / "cache" / "run"          # empty cwd: CLIs pick up no project files
    providers = {
        "demo": Demo(),
        "claude": ClaudeCLI(settings.claude_cmd, work, settings.reply_timeout),
        "codex": CodexCLI(settings.codex_cmd, work, settings.reply_timeout),
        "anthropic_api": AnthropicAPI(Accounts.api_key, settings.anthropic_base_url, api_transport,
                                      settings.reply_timeout),
    }

    # ---------------------------------------------------------------- lifecycle
    def shutdown_handoffs() -> None:
        """Best effort: closing the console window kills the process without this running,
        which is why handoffs are also written every BACKUP_EVERY replies."""
        for p in store.list_projects():
            try:
                d = store.project_dir(p["id"])
                chat_file, latest = d / "chat.jsonl", d / "handoff.md"
                if chat_file.exists() and (not latest.exists() or chat_file.stat().st_mtime > latest.stat().st_mtime):
                    write_handoff(p["id"], "shutdown", None)
            except Exception:
                log.exception("shutdown handoff failed for %s", p.get("id"))

    async def daily_backups():
        """A daily backup whenever the app is running and the last one is a day old (R11)."""
        await asyncio.sleep(backup_delay)
        while True:
            try:
                if backups.due() and not backups.busy():
                    info = await asyncio.to_thread(backups.create, "daily")
                    log.info("daily backup written: %s", info["name"])
            except Exception:                          # noqa: BLE001 - never take the app down
                log.exception("daily backup failed")
            await asyncio.sleep(backup_tick)

    async def gpu_info(refresh: bool = False) -> dict | None:
        """The first NVIDIA card (cached: nvidia-smi takes a moment)."""
        if refresh or "gpu" not in gpu_cache:
            if gpu_probe is not None:
                gpu_cache["gpu"] = gpu_probe()
            else:
                g = await system.gpu(system.run)
                gpu_cache["gpu"] = (g.get("gpus") or [None])[0]
        return gpu_cache["gpu"]

    async def reconfigure_voice() -> None:
        voice_svc.configure(*modules.voice_config(await gpu_info()))

    @asynccontextmanager
    async def lifespan(_app):
        try:
            await reconfigure_voice()
        except Exception:                              # noqa: BLE001 - voice is optional
            log.exception("voice module check failed")
        ticker = asyncio.get_running_loop().create_task(daily_backups())
        yield
        ticker.cancel()
        shutdown_handoffs()
        await asyncio.to_thread(knowledge.close)

    app = FastAPI(title="One Production", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    OPEN_WHEN_LOCKED = {("GET", "/api/profile"), ("POST", "/api/profile/unlock")}

    @app.middleware("http")
    async def pin_lock(request, call_next):
        if (request.url.path.startswith("/api/") and profile.locked
                and (request.method, request.url.path) not in OPEN_WHEN_LOCKED):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "locked - enter your PIN"}, status_code=423)
        return await call_next(request)

    app.add_middleware(GuardMiddleware, token=token)
    app.state.token = token          # read by the launcher only; never sent in a response
    app.state.resolve = resolve      # tests swap in a stand-in Resolve helper

    def _project(pid: str) -> None:
        try:
            store.files_dir(pid)
        except NotFound:
            raise HTTPException(404, "project not found")

    mount_knowledge(app, knowledge, _project, WEB_DIR)
    mount_learning(app, learning, _project)
    mount_resolve(app, resolve, _project, WEB_DIR)
    mount_gates(app, gates, _project)
    mount_editor(app, editor, approvals, _project, animation)

    def _account(acc_id: str) -> dict:
        if acc_id == "demo":
            return DEMO_ACCOUNT
        try:
            return accounts.get(acc_id)
        except AccountError:
            raise HTTPException(404, "account not found")

    async def _check(acc: dict) -> dict:
        try:
            res = await providers[acc["provider"]].status(acc)
        except Exception as e:          # a broken CLI must not break the accounts page
            res = {"ok": False, "reason": f"check failed: {e.__class__.__name__}"}
        return accounts.record_status(acc["id"], res)

    # ---------------------------------------------------------------- usage helpers
    def usage_pct(acc: dict) -> tuple[float | None, str]:
        """(percent used, "exact" | "estimated" | "none"). Exact when the provider reports its
        own utilisation (Claude); estimated from tokens against a user-set 5-hour budget."""
        if acc["id"] == "demo":
            return None, "none"
        rl = acc.get("rate_limit") or {}
        now = time.time()
        windows = []
        for w in (rl.get("windows") or {}).values():
            if w.get("utilization") is None:
                continue
            # a window whose reset time has passed has started again from zero
            windows.append(0.0 if w.get("resets_at") and w["resets_at"] <= now else w["utilization"])
        if windows and now - rl.get("at", 0) < 7 * 86400:
            return round(max(windows) * 100, 1), "exact"
        budget = acc.get("budget_tokens_5h")
        if budget:
            used = ledger.tokens_since(acc["id"], time.time() - 5 * 3600)
            return round(100 * used / budget, 1), "estimated"
        return None, "none"

    def spend_today(acc: dict) -> dict:
        tok, usd = ledger.spent_since(acc["id"], range_start("today"))
        return {"tokens": tok, "cost_usd": round(usd, 4)}

    def over_cap(acc: dict) -> str | None:
        """Why this account may not take more work today (R10 daily caps), or None."""
        if not (acc.get("cap_day_tokens") or acc.get("cap_day_usd")):
            return None
        t = spend_today(acc)
        if acc.get("cap_day_tokens") and t["tokens"] >= acc["cap_day_tokens"]:
            return f"daily cap of {acc['cap_day_tokens']:,} tokens reached"
        if acc.get("cap_day_usd") and t["cost_usd"] >= acc["cap_day_usd"]:
            return f"daily cap of ${acc['cap_day_usd']:.2f} reached"
        return None

    def eligible(from_acc: dict) -> list[dict]:
        """Every other account, each with whether it can take over and why not."""
        out = []
        for a in accounts.list():
            if a["id"] == from_acc["id"]:
                continue
            pct, kind = usage_pct(a)
            why = None
            if not a["enabled"]:
                why = "turned off"
            elif a["status"] != "ok":
                why = a.get("reason") or "not signed in"
            elif not switch_allowed(from_acc, a):
                why = ("switching between two accounts of the same service is off until the terms "
                       "check (plan R8)")
            elif pct is not None and pct >= a.get("switch_pct", DEFAULT_SWITCH):
                why = f"also at {pct:.0f}%"
            out.append({"id": a["id"], "label": a.get("label") or a.get("email") or a["provider"],
                        "provider": a["provider"], "usage_pct": pct, "usage_kind": kind,
                        "ok": why is None, "why": why, "resets_at": resets_of(a)})
        return sorted(out, key=lambda x: not x["ok"])

    def resets_of(acc: dict):
        """Earliest reset time the provider reported for this account (Claude does)."""
        ts = [w.get("resets_at") for w in ((acc.get("rate_limit") or {}).get("windows") or {}).values()
              if w.get("resets_at")]
        return min(ts) if ts else None

    def write_handoff(pid: str, reason: str, acc: dict | None) -> Path:
        return handoff.write(store.project_dir(pid), store.project(pid), store.history(pid),
                             store.list_files(pid), reason=reason, account=acc)

    # ---------------------------------------------------------------- the reply pipeline
    def after_reply(pid: str, acc: dict, error) -> list[dict]:
        """Switch / backup decisions after any reply, chat or agent (plan R3, D6)."""
        if acc["id"] == "demo":
            return []
        fresh = accounts.get(acc["id"])
        pct, kind = usage_pct(fresh)
        reason = None
        if error and error.kind == "limit":
            ledger.record_limit_hit(acc["id"], error.message, pct if kind == "estimated" else
                                    (100.0 if kind == "exact" else None))
            reason = "limit"
        elif pct is not None and pct >= fresh.get("switch_pct", DEFAULT_SWITCH):
            reason = "threshold"
        if reason:
            path = write_handoff(pid, reason, fresh)
            return [{"t": "switch_offer", "reason": reason, "from": acc["id"], "usage_pct": pct,
                     "usage_kind": kind, "handoff": path.name, "from_resets_at": resets_of(fresh),
                     "eligible": eligible(fresh)}]
        replies = sum(1 for m in store.history(pid) if m["role"] == "assistant")
        if replies and replies % handoff.BACKUP_EVERY == 0:
            write_handoff(pid, "backup", fresh)
        return []

    def run_reply(pid: str, acc: dict, prompt: str, studio: tuple | None = None):
        """Stream one reply, save it, record usage, and decide whether to offer a switch."""
        provider = providers[acc["provider"]]
        state = {"windows": None}

        def on_ratelimit(ev: RateLimit):
            state["windows"] = ev.windows
            if acc["id"] != "demo":
                accounts.record_rate_limit(acc["id"], ev.windows, ev.status)

        def on_auth(ev: Fail):
            if acc["id"] != "demo":
                accounts.mark_auth_error(acc["id"], ev.message)

        async def stream():
            parts, usage, error = [], None, None
            try:
                async for line, ev in reply_events(provider, acc, prompt, acc.get("model"),
                                                   on_ratelimit, on_auth, effort_of(acc)):
                    if isinstance(ev, Text):
                        parts.append(ev.text)
                    elif isinstance(ev, Usage):
                        usage = ev
                    elif isinstance(ev, Fail):
                        error = ev
                    yield line
            except Exception as e:      # never leave the browser hanging on a broken stream
                log.exception("chat stream failed")
                error = Fail("fatal", f"internal error: {e.__class__.__name__}", interrupted=bool(parts))
                yield ndjson({"t": "error", "kind": "fatal", "message": error.message,
                              "interrupted": error.interrupted})

            meta = {"account": acc["id"], "provider": acc["provider"]}
            if usage:
                meta["usage"] = {"in": usage.input_tokens, "out": usage.output_tokens,
                                 "cost_usd": usage.cost_usd, "model": usage.model}
            if error:
                meta["error"] = {"kind": error.kind, "message": error.message}
                meta["interrupted"] = error.interrupted
            text = "".join(parts).strip()
            if studio and not error:
                text, action = actions.extract(text)
                if action:
                    meta["action"] = actions.check(action, studio[0], studio[1], studio[2])
                    meta["time"] = time.time()          # the Run card refers back to this message
                    yield ndjson({"t": "action", "text": text, "action": meta["action"], "time": meta["time"]})
            if text or error:
                store.append_message(pid, "assistant", text, **meta)
            ledger.record_call(account=acc["id"], provider=acc["provider"], project=pid, usage=usage,
                               outcome="error" if error else "ok", err_kind=error.kind if error else None,
                               windows=state["windows"])

            for ev in after_reply(pid, acc, error):
                yield ndjson(ev)
            yield ndjson({"t": "done"})

        return StreamingResponse(stream(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store"})

    # ---------------------------------------------------------------- public
    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__,
                "data_dir": str(settings.data_dir), "python": platform.python_version()}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html", media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "no-store"})

    # ---------------------------------------------------------------- projects
    @app.get("/api/projects")
    def list_projects() -> list[dict]:
        return store.list_projects()

    @app.post("/api/projects")
    def create_project(body: NewProject) -> dict:
        return store.create_project(body.name)

    @app.get("/api/projects/{pid}")
    def get_project(pid: str) -> dict:
        _project(pid)
        return store.project(pid)

    @app.delete("/api/projects/{pid}")
    def delete_project(pid: str) -> dict:
        _project(pid)
        if any(j.get("project") == pid and j.get("status") not in ("done", "failed")
               for j in voice_jobs.jobs.values()):
            raise HTTPException(409, "a voiceover is still running in this project - wait for it to finish")
        dst = store.delete_project(pid)
        return {"deleted": pid, "trash": str(dst)}

    # ---------------------------------------------------------------- files
    @app.get("/api/projects/{pid}/files")
    def list_files(pid: str) -> list[dict]:
        _project(pid)
        return store.list_files(pid)

    @app.post("/api/projects/{pid}/files")
    async def upload(pid: str, files: list[UploadFile] = File(...)) -> list[dict]:
        _project(pid)
        saved = []
        for up in files:
            target = store.unique_path(pid, up.filename or "file")
            partial = target.with_name(target.name + ".part")
            try:
                with partial.open("wb") as out:
                    while chunk := await up.read(UPLOAD_CHUNK):
                        out.write(chunk)
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)
            saved.append({"name": target.name, "size": target.stat().st_size})
        return saved

    @app.get("/api/projects/{pid}/files/{name:path}")
    def download(pid: str, name: str) -> FileResponse:
        _project(pid)
        try:
            path = store.file_path(pid, name)
        except NotFound:
            raise HTTPException(404, "file not found")
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    # ---------------------------------------------------------------- accounts (R1)
    @app.get("/api/providers")
    def list_providers() -> dict:
        return PROVIDERS

    def _public(a: dict) -> dict:
        out = Accounts.public(a)
        if a["id"] != "demo":
            out["usage_pct"], out["usage_kind"] = usage_pct(a)
            out.setdefault("warn_pct", DEFAULT_WARN)
            out.setdefault("switch_pct", DEFAULT_SWITCH)
        return out

    @app.get("/api/accounts")
    def list_accounts() -> list[dict]:
        return [DEMO_ACCOUNT] + [_public(a) for a in accounts.list()]

    @app.post("/api/accounts")
    async def add_account(body: NewAccount) -> dict:
        if body.provider == "anthropic_api":
            check = await providers["anthropic_api"].check_key((body.api_key or "").strip())
            if not check["ok"]:
                raise HTTPException(400, f"That API key didn't work: {check['reason']}")
        try:
            acc = accounts.add(body.provider, body.label, body.api_key, body.model)
        except AccountError as e:
            raise HTTPException(400, str(e))
        if body.provider == "anthropic_api":
            acc = accounts.record_status(acc["id"], {"ok": True, "method": "API key"})
        return _public(acc)

    @app.post("/api/accounts/check")
    async def check_all() -> list[dict]:
        accs = [a for a in accounts.list() if a["enabled"]]
        await asyncio.gather(*(_check(a) for a in accs))
        return list_accounts()

    @app.post("/api/accounts/order")
    def reorder(body: OrderIn) -> list[dict]:
        try:
            accounts.reorder(body.ids)
        except AccountError as e:
            raise HTTPException(400, str(e))
        return list_accounts()

    @app.post("/api/accounts/{acc_id}/check")
    async def check_one(acc_id: str) -> dict:
        if acc_id == "demo":
            return DEMO_ACCOUNT
        return _public(await _check(_account(acc_id)))

    @app.post("/api/accounts/{acc_id}/login")
    def open_login(acc_id: str) -> dict:
        """Open the provider's own login in a separate window, bound to this account's folder.
        The user signs in there; the page then polls /check."""
        acc = _account(acc_id)
        prov = providers[acc["provider"]]
        argv = getattr(prov, "login_argv", lambda: None)()
        if not argv:
            raise HTTPException(400, f"{PROVIDERS.get(acc['provider'], {}).get('label', 'This')} login "
                                     "is not available on this PC")
        env = {**os.environ, **prov.env(acc)}
        flags = subprocess.CREATE_NEW_CONSOLE if login_console else subprocess.CREATE_NO_WINDOW
        work.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(argv, env=env, cwd=str(work), creationflags=flags,
                         stdin=None if login_console else subprocess.DEVNULL)
        return {"started": True}

    @app.get("/api/accounts/{acc_id}/models")
    async def account_models(acc_id: str, refresh: bool = False) -> dict:
        """Model + effort choices for the pickers. Codex lists its own (cached; refresh asks it)."""
        if acc_id == "demo":
            return {**M.choices("demo"), "model": None, "effort": None}
        acc = _account(acc_id)
        note = None
        if acc["provider"] == "codex" and (refresh or acc.get("codex_models") is None):
            try:
                raw = await codex_agent.list_models(acc, settings.data_dir / "cache" / "run")
                acc = accounts.set_codex_models(acc_id, M.codex_models(raw))
            except Exception as e:                   # noqa: BLE001 - keep the last list, say why
                note = f"could not ask Codex for its models: {str(e)[:120] or e.__class__.__name__}"
        return {**M.choices(acc["provider"], acc.get("codex_models")), "model": acc.get("model"),
                "effort": acc.get("effort"), "note": note}

    @app.patch("/api/accounts/{acc_id}")
    def patch_account(acc_id: str, body: AccountPatch) -> dict:
        if acc_id == "demo":
            raise HTTPException(400, "the Demo account is built in")
        _account(acc_id)
        try:
            if body.label is not None:
                accounts.rename(acc_id, body.label)
            if body.enabled is not None:
                accounts.set_enabled(acc_id, body.enabled)
            if body.model is not None:
                accounts.set_model(acc_id, body.model)
            if body.effort is not None:
                accounts.set_effort(acc_id, body.effort)
            if body.warn_pct is not None or body.switch_pct is not None or body.budget_tokens_5h != -1:
                accounts.set_limits(acc_id, body.warn_pct, body.switch_pct, body.budget_tokens_5h)
            if body.caps is not None:
                accounts.set_caps(acc_id, body.caps)
        except AccountError as e:
            raise HTTPException(400, str(e))
        return _public(accounts.get(acc_id))

    @app.delete("/api/accounts/{acc_id}")
    def delete_account(acc_id: str) -> dict:
        if acc_id == "demo":
            raise HTTPException(400, "the Demo account is built in")
        _account(acc_id)
        accounts.remove(acc_id)
        return {"removed": acc_id}

    # ---------------------------------------------------------------- usage page (R2)
    @app.get("/api/usage")
    def usage(range: str = "today") -> dict:
        if range not in RANGES:
            raise HTTPException(400, "range must be one of: " + ", ".join(RANGES))
        s = ledger.summary(range)
        cards = []
        for a in accounts.list():
            pct, kind = usage_pct(a)
            row = s["accounts"].get(a["id"], {})
            cards.append({**_public(a), "requests": row.get("requests") or 0,
                          "in_tok": row.get("in_tok") or 0, "out_tok": row.get("out_tok") or 0,
                          "cost_usd": row.get("cost_usd"), "last_used": row.get("last_used"),
                          "errors": row.get("errors") or 0, "usage_pct": pct, "usage_kind": kind,
                          "accuracy": ledger.estimate_accuracy(a["id"]) if kind == "estimated" else None,
                          "caps": {k: a.get(k) for k in CAP_FIELDS}, "today": spend_today(a)})
        return {"range": range, "since": s["since"], "cards": cards, "totals": s["totals"],
                "by_project": s["by_project"], "by_model": s["by_model"], "by_role": s["by_role"],
                "same_service_switching": SAME_SERVICE_SWITCHING}

    async def _refresh_one(a: dict) -> None:
        prov = providers.get(a["provider"])
        if not hasattr(prov, "live_usage") or a.get("live_usage_off"):
            return
        try:
            res = await prov.live_usage(a)
        except Exception:
            log.exception("live usage failed for %s", a["id"])
            return
        if not res:
            return
        if res.get("error"):
            accounts.stop_live_usage(a["id"], res["error"])
            return
        accounts.record_rate_limit(a["id"], res["windows"], "allowed", at=res.get("as_of"))

    @app.post("/api/usage/refresh")
    async def refresh_usage() -> list[dict]:
        """Read every account's current limits without sending a message: Claude answers
        `/usage` locally, Codex keeps them in its own session files."""
        accs = [a for a in accounts.list() if a["enabled"] and a["status"] == "ok"]
        await asyncio.gather(*(_refresh_one(a) for a in accs))
        return list_accounts()

    @app.get("/api/usage/switches")
    def switch_log() -> list[dict]:
        return ledger.switches()

    @app.get("/api/usage/limit-hits")
    def limit_log() -> list[dict]:
        return ledger.limit_hits()

    @app.get("/api/usage/export.csv")
    def export_csv(range: str = "month") -> PlainTextResponse:
        if range not in RANGES:
            raise HTTPException(400, "unknown range")
        now = time.time()

        def window(a: dict, name: str) -> tuple:
            w = ((a.get("rate_limit") or {}).get("windows") or {}).get(name) or {}
            if w.get("utilization") is None:
                return None, None
            expired = w.get("resets_at") and w["resets_at"] <= now      # started again from zero
            return (0.0 if expired else round(w["utilization"] * 100, 1)), (None if expired else w.get("resets_at"))

        rows, full = [], {a["id"]: a for a in accounts.list()}
        for card in usage(range)["cards"]:
            a = full.get(card["id"], {})
            (s5, r5), (s7, r7) = window(a, "five_hour"), window(a, "seven_day")
            rows.append({**card, "used_now_pct": card["usage_pct"], "measured": card["usage_kind"],
                         "session_5h_pct": s5, "session_resets": r5, "week_pct": s7, "week_resets": r7})
        # the BOM makes Excel read the file as UTF-8 (otherwise dashes and accents come out garbled)
        return PlainTextResponse("\ufeff" + ledger.export_csv(range, redact, rows),
                                 media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="usage-{range}.csv"'})

    # ---------------------------------------------------------------- chat
    @app.get("/api/projects/{pid}/chat")
    def history(pid: str) -> list[dict]:
        _project(pid)
        return store.history(pid)

    @app.post("/api/projects/{pid}/chat")
    async def chat(pid: str, body: ChatIn) -> StreamingResponse:
        _project(pid)
        acc = _account(body.account_id or "demo")
        if not acc["enabled"]:
            raise HTTPException(400, "that account is turned off")
        if settings.agent_mode and acc["provider"] in AGENT_PROVIDERS and agent_ready(acc):
            return await run_agent(pid, acc, body.message)
        studio = await studio_state(pid)
        prompt = build_prompt(store.history(pid), body.message,
                              actions.context(*studio) + "\n\n" + actions.HELP)
        store.append_message(pid, "user", body.message)
        return run_reply(pid, acc, prompt, studio)

    async def studio_state(pid: str) -> tuple:
        """(files, voices or None, presets, jobs). Never waits on the voice engine: it uses the
        voice list from the last time the app talked to it, and refreshes that in the background
        (a stopped engine costs ~2 s to detect on Windows, which would delay every reply)."""
        meta = voice_svc.last_meta
        if meta is None and not voice_svc.missing():
            async def probe():
                if await voice_svc.running():
                    await voice_svc.meta()
            bg = asyncio.get_running_loop().create_task(probe())
            background.add(bg)
            bg.add_done_callback(background.discard)
        voices = [v for v in meta.get("voices", []) if not v.startswith("_upload_")] if meta else None
        return store.list_files(pid), voices, (meta or {}).get("presets", {}), voice_jobs.for_project(pid)

    background: set = set()

    # ---------------------------------------------------------------- backups (R11)
    @app.get("/api/backups")
    def list_backups() -> dict:
        return {"settings": backups.settings(), "backups": backups.list(), "running": backups.busy()}

    @app.post("/api/backups")
    async def make_backup(body: BackupIn) -> dict:
        try:
            return await asyncio.to_thread(backups.create, "manual", body.include_media)
        except Busy as e:
            raise HTTPException(409, str(e))
        except (BackupError, OSError) as e:
            raise HTTPException(400, f"backup failed: {e}")

    @app.patch("/api/backups/settings")
    def backup_settings(body: BackupSettingsIn) -> dict:
        try:
            return backups.update(body.folder, body.include_media, body.keep)
        except BackupError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/backups/{name}/restore")
    async def restore_backup(name: str, body: RestoreIn) -> dict:
        if runs.busy(body.project):
            raise HTTPException(409, "the agent is working in that project - wait or press Stop first")
        if any(j.get("project") == body.project and j.get("status") not in ("done", "failed")
               for j in voice_jobs.jobs.values()):
            raise HTTPException(409, "a voiceover is still running in that project")
        try:
            return await asyncio.to_thread(backups.restore, name, body.project)
        except Busy as e:
            raise HTTPException(409, str(e))
        except BackupError as e:
            raise HTTPException(400, str(e))

    # ---------------------------------------------------------------- modules + install (R13/R14)
    @app.get("/api/modules")
    async def list_modules() -> dict:
        gpu = await gpu_info()
        return {"modules": modules.status(gpu, installer.running() and installer.module == "voice"),
                "gpu": gpu}

    @app.patch("/api/modules/voice")
    async def set_voice_mode(body: VoiceModeIn) -> dict:
        if installer.running():
            raise HTTPException(409, "an install is running - wait for it or press Cancel")
        try:
            modules.set_voice(body.mode)
        except ValueError as e:
            raise HTTPException(400, str(e))
        await reconfigure_voice()
        return await list_modules()

    def voice_items(gpu, mode=None):
        r = modules.recipe(gpu, mode)
        return r, r.items()

    @app.get("/api/install/plan")
    async def install_plan(module: str = "voice") -> dict:
        if module != "voice":
            raise HTTPException(404, "only the voice module installs anything")
        gpu = await gpu_info()
        if modules.external():
            return {"module": "voice", "external": True, "items": []}
        if modules.voice_mode(gpu) == "off":
            raise HTTPException(400, "switch the voice module on first")
        r, items = voice_items(gpu)
        return {"module": "voice", "mode": r.mode, "torch_build": r.torch_kind, "ready": r.ready(),
                "items": installer.plan("voice", items)}

    @app.post("/api/install")
    async def install(body: InstallIn) -> dict:
        gpu = await gpu_info()
        if installer.module != "voice" or not installer.items:
            await install_plan("voice")
        r = modules.recipe(gpu)
        loop = asyncio.get_running_loop()

        def done(result):
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(reconfigure_voice()))
        try:
            installer.start(body.approve, verify=r.verify, on_done=done)
        except InstallError as e:
            raise HTTPException(409 if installer.running() else 400, str(e))
        return installer.state()

    @app.get("/api/install")
    def install_state() -> dict:
        return installer.state()

    @app.post("/api/install/cancel")
    def install_cancel() -> dict:
        return {"cancelled": installer.cancel()}

    @app.get("/api/setup")
    def get_setup() -> dict:
        return {**setup_state.public(), "drives": drives(), "can_restart": restart is not None}

    @app.patch("/api/setup")
    def patch_setup(body: SetupIn) -> dict:
        return setup_state.save(step=body.step, done=body.done)

    @app.post("/api/setup/data-folder")
    async def choose_data_folder(body: FolderIn) -> dict:
        raw = body.path.strip().strip('"')
        target = Path(os.path.expandvars(raw)).expanduser()
        if not target.is_absolute():
            raise HTTPException(400, "give the full folder path, e.g. D:\\OneProduction")
        if target.resolve() == settings.data_dir.resolve():
            setup_state.save(chose_folder=True, step=1)
            return {"restarting": False, **setup_state.public()}
        if not setup_state.first_run() and (store.list_projects() or accounts.list()):
            raise HTTPException(409, "this copy already has projects or accounts - moving them isn't automatic yet")
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".write-test"
            probe.write_text("ok")
            probe.unlink()
        except OSError as e:
            raise HTTPException(400, f"can't use that folder: {e.strerror or e}")
        moved = settings.model_copy(update={"data_dir": target})
        moved.ensure_dirs()
        SetupState(target, store, accounts).save(chose_folder=True, step=1)
        save_setting("data_dir", str(target))
        if restart is None:
            return {"restarting": False, "saved": True, "data_dir": str(target),
                    "message": "Saved - close One Production and start it again to use the new folder."}
        asyncio.get_running_loop().call_later(0.8, restart)
        return {"restarting": True, "data_dir": str(target)}

    @app.post("/api/voice/tune")
    async def tune_voice(body: TuneIn) -> dict:
        need = approvals.check("voice_tune", body.approve)
        if need:
            from fastapi.responses import JSONResponse
            return JSONResponse({"needs_approval": need}, status_code=409)
        if tuner.state.get("running"):
            raise HTTPException(409, "already tuning a voice")
        try:
            await voice_svc.ensure_running()
            meta = await voice_svc.meta()
        except VoiceError as e:
            raise HTTPException(400, str(e))
        if body.voice not in meta.get("voices", []):
            raise HTTPException(404, "no such voice")
        task = asyncio.get_running_loop().create_task(tuner.run(body.voice, meta.get("voices_dir")))
        background.add(task)
        task.add_done_callback(background.discard)
        await asyncio.sleep(0)
        return tuner.state

    @app.get("/api/voice/tune")
    def tune_state() -> dict:
        return {**tuner.state, "tuned": {k: v.get("best") for k, v in tuner.tuned().items()}}

    # ---------------------------------------------------------------- updates (R15)
    def _update_error(e: Exception):
        raise HTTPException(400, str(e))

    @app.get("/api/update")
    def update_info() -> dict:
        return {**updater.info(), "migration": migration, "can_relaunch": relaunch is not None}

    @app.post("/api/update/check")
    async def update_check() -> dict:
        try:
            return await asyncio.to_thread(updater.check)
        except InstallError as e:
            _update_error(e)

    @app.patch("/api/update/source")
    def update_source(body: SourceIn) -> dict:
        try:
            return updater.set_source(body.source)
        except InstallError as e:
            _update_error(e)

    @app.post("/api/update/download")
    async def update_download() -> dict:
        try:
            return await asyncio.to_thread(updater.start_download)
        except InstallError as e:
            _update_error(e)

    @app.post("/api/update/cancel")
    def update_cancel() -> dict:
        return {"cancelled": updater.cancel()}

    def _switch_and_relaunch(fn) -> dict:
        try:
            info = fn()
        except InstallError as e:
            _update_error(e)
        if relaunch is None:
            return {**info, "relaunching": False,
                    "message": "Switched - close One Production and start it again to use it."}
        asyncio.get_running_loop().call_later(0.8, relaunch)
        return {**info, "relaunching": True}

    @app.post("/api/update/apply")
    async def update_apply() -> dict:                   # async: the relaunch is scheduled on the loop
        return _switch_and_relaunch(updater.apply)

    @app.post("/api/update/rollback")
    async def update_rollback() -> dict:
        return _switch_and_relaunch(updater.rollback)

    # ---------------------------------------------------------------- support (R16)
    @app.get("/api/diagnostics")
    async def export_diagnostics():
        from fastapi.responses import Response
        scan = await system.scan(settings, [_public(a) for a in accounts.list()], voice_svc, backups)
        sections = {"system-scan": scan, "modules": modules.status(await gpu_info()),
                    "setup": setup_state.public(), "backups": {"settings": backups.settings(),
                                                               "list": backups.list()[:5]},
                    "update": updater.info(), "install": installer.state(), "migration": migration,
                    "accounts": [{k: a.get(k) for k in ("provider", "label", "email", "plan", "status", "reason",
                                                        "enabled", "usage_pct")} for a in
                                 [_public(x) for x in accounts.list()]]}
        data = await asyncio.to_thread(diagnostics.build, settings.data_dir, settings_file(), store, redact, sections)
        name = f"one-production-diagnostics-{time.strftime('%Y%m%d-%H%M')}.zip"
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/api/sample")
    def add_sample() -> dict:
        """The 'Getting started' project: a short script ready to voice (R16)."""
        found = next((p for p in store.list_projects() if p.get("sample")), None)
        if found:
            return found
        meta = store.create_project("Getting started")
        meta["sample"] = True
        (store.project_dir(meta["id"]) / "project.json").write_text(json.dumps(meta), encoding="utf-8")
        for f in sorted(SAMPLES.glob("*")):
            if f.is_file():
                shutil.copy2(f, store.files_dir(meta["id"]) / f.name)
        return meta

    @app.post("/api/shortcuts")
    async def make_shortcuts() -> dict:
        """Start menu + desktop shortcuts to the launcher (packaged copies only, on request)."""
        if not updater.packaged:
            raise HTTPException(400, "shortcuts are for the installed package, not a development copy")
        target = updater.root / "One Production.cmd"
        made = []
        for folder in (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
                       Path(os.environ.get("USERPROFILE", "")) / "Desktop"):
            if not folder.is_dir():
                continue
            lnk = folder / "One Production.lnk"
            ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:OP_LNK);"
                  "$s.TargetPath=$env:OP_TARGET;$s.WorkingDirectory=$env:OP_DIR;"
                  "$s.Description='One Production';$s.Save()")
            r = await asyncio.to_thread(subprocess.run, ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                                        capture_output=True, text=True, timeout=30,
                                        env={**os.environ, "OP_LNK": str(lnk), "OP_TARGET": str(target),
                                             "OP_DIR": str(updater.root)})
            if r.returncode == 0 and lnk.exists():
                made.append(str(lnk))
        return {"made": made}

    # ---------------------------------------------------------------- system scan (R13 step 2)
    @app.get("/api/system")
    async def system_scan() -> dict:
        return await system.scan(settings, [_public(a) for a in accounts.list()], voice_svc, backups)

    # ---------------------------------------------------------------- profile + PIN (R7)
    @app.get("/api/profile")
    def get_profile() -> dict:
        return profile.public()

    @app.patch("/api/profile")
    def set_profile(body: ProfileIn) -> dict:
        return profile.set_name(body.name)

    @app.post("/api/profile/pin")
    def set_pin(body: PinIn) -> dict:
        try:
            return profile.set_pin(body.current, body.new)
        except Throttled as e:
            raise HTTPException(429, str(e))
        except ProfileError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/profile/unlock")
    def unlock(body: UnlockIn) -> dict:
        try:
            return profile.unlock(body.pin)
        except Throttled as e:
            raise HTTPException(429, str(e))
        except ProfileError as e:
            raise HTTPException(403, str(e))

    @app.post("/api/profile/lock")
    def lock_now() -> dict:
        return profile.lock()

    # ---------------------------------------------------------------- agent mode (plan P5)
    def sessions_file(pid: str) -> Path:
        return store.project_dir(pid) / "agent-sessions.json"

    def agent_session(pid: str, acc_id: str) -> str | None:
        f = sessions_file(pid)
        return (json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}).get(acc_id)

    def save_agent_session(pid: str, acc_id: str, sid: str | None) -> None:
        if not sid:
            return
        f = sessions_file(pid)
        data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        data[acc_id] = sid
        f.write_text(json.dumps(data), encoding="utf-8")

    def voice_gate(remember: bool = False, tool: str = "voiceover"):
        """The R12 heavy-job gate for agents' gated tools (voiceover, video render)."""
        if remember:
            approvals.check(tool, "always")
            return None
        return approvals.check(tool, None)

    def agent_ready(acc: dict) -> bool:
        """Agent mode needs the official client on this PC (Claude Code for Claude logins and
        API keys, Codex for Codex logins). An API key without Claude Code still chats (P7)."""
        if acc["provider"] in ("claude", "anthropic_api"):
            from .agent.claude_agent import claude_exe
            return bool(claude_exe(settings.claude_cmd)) or agent_client is not None
        if acc["provider"] == "codex":
            from .providers.cli import resolve
            return bool(resolve("codex", settings.codex_cmd)) or codex_transport is not None
        return False

    def effort_of(acc: dict) -> str | None:
        return M.effort_for(acc["provider"], acc.get("model"), acc.get("effort"), acc.get("codex_models"))

    def agent_for(acc: dict):
        return claude_agent if acc["provider"] in ("claude", "anthropic_api") else codex_agent

    async def run_agent(pid: str, acc: dict, message: str) -> StreamingResponse:
        root = store.files_dir(pid)
        try:
            run = runs.start(pid, root)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        sid = agent_session(pid, acc["id"])
        files, voices, presets, jobs = await studio_state(pid)
        instructions = STUDIO_INSTRUCTIONS + "\n\n" + actions.context(files, voices, presets, jobs)
        instructions += "\n\n" + learning.instructions(pid, message)
        # a fresh session (new project, or another account taking over) gets the recent chat
        prompt = message if sid else build_prompt(store.history(pid), message).replace(SYSTEM + "\n\n", "", 1)
        store.append_message(pid, "user", message)
        studio = Studio(pid, store, voice_svc, voice_jobs, tuned=tuner.for_voice, knowledge=knowledge, learning=learning, resolve=resolve, gates=gates, editor=editor, animation=animation,
                        on_job=lambda job: run.emit({"t": "voicejob", "id": job["id"], "source": job["source"],
                                                     "voice": job["voice"]}))

        async def work():
            res = {"error": None, "text": "", "steps": [], "usage": None, "windows": None, "session_id": sid}
            try:
                res = await agent_for(acc).run(run, runs, acc, prompt, sid, instructions, studio,
                                               acc.get("model"), voice_gate, effort=effort_of(acc))
            except asyncio.CancelledError:
                res["error"] = Fail("fatal", "Stopped.")
            except Exception as e:                       # noqa: BLE001
                log.exception("agent run failed")
                res["error"] = Fail("fatal", f"internal error: {e.__class__.__name__}")
            err, usage = res["error"], res["usage"]
            if res["windows"] and acc["provider"] in ("claude", "codex"):      # API keys report none
                accounts.record_rate_limit(acc["id"], res["windows"], "allowed")
            if err and err.kind == "auth":
                accounts.mark_auth_error(acc["id"], err.message)
            meta = {"account": acc["id"], "provider": acc["provider"], "agent": True, "steps": res["steps"]}
            if usage:
                meta["usage"] = {"in": usage.input_tokens, "out": usage.output_tokens,
                                 "cost_usd": usage.cost_usd, "model": usage.model}
                run.emit({"t": "usage", "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                          "model": usage.model})
            if err:
                meta["error"] = {"kind": err.kind, "message": err.message}
                meta["interrupted"] = err.interrupted
                run.emit({"t": "error", "kind": err.kind, "message": err.message, "interrupted": err.interrupted})
            store.append_message(pid, "assistant", res["text"], **meta)
            save_agent_session(pid, acc["id"], res.get("session_id"))
            ledger.record_call(account=acc["id"], provider=acc["provider"], project=pid, usage=usage,
                               outcome="error" if err else "ok", err_kind=err.kind if err else None,
                               windows=res["windows"], role="agent")
            for ev in after_reply(pid, acc, err):
                run.emit(ev)
            runs.finish(run)
            run.emit({"t": "done"})

        run.task = asyncio.get_running_loop().create_task(work())
        return StreamingResponse(follow(run), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})

    async def follow(run):
        q = run.listen()
        try:
            while True:
                ev = await q.get()
                yield ndjson(ev)
                if ev["t"] == "done":
                    break
        finally:
            if q in run.listeners:
                run.listeners.remove(q)

    @app.get("/api/projects/{pid}/agent")
    def agent_state(pid: str) -> dict:
        _project(pid)
        run = runs.busy(pid)
        return {"running": bool(run), "asks": list(run.asks.values()) if run else [],
                "session_allow": sorted(runs.session_allow.get(pid, set()))}

    @app.get("/api/projects/{pid}/agent/stream")
    def agent_stream(pid: str) -> StreamingResponse:
        """Re-attach to a run that is still working (after switching projects or a reload)."""
        _project(pid)
        run = runs.busy(pid)
        if not run:
            raise HTTPException(404, "the agent isn't working in this project")
        return StreamingResponse(follow(run), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})

    @app.post("/api/projects/{pid}/agent/answer")
    def agent_answer(pid: str, body: AgentAnswer) -> dict:
        _project(pid)
        if body.answer not in ("once", "session", "always", "deny"):
            raise HTTPException(400, "answer must be once, session, always or deny")
        if not runs.answer(pid, body.id, body.answer):
            raise HTTPException(404, "that question is no longer waiting")
        return {"ok": True}

    @app.post("/api/projects/{pid}/agent/stop")
    def agent_stop(pid: str) -> dict:
        _project(pid)
        return {"stopped": runs.stop(pid)}

    # ---------------------------------------------------------------- team mode (P9: R4, R10)
    team_file = settings.data_dir / "team_settings.json"
    teams: dict[str, Team] = {}

    def team_settings() -> dict:
        try:
            saved = json.loads(team_file.read_text(encoding="utf-8")) if team_file.exists() else {}
        except ValueError:
            saved = {}
        base = {k: getattr(TeamConfig(), k) for k in TeamConfig.__dataclass_fields__}
        return {**base, **{k: v for k, v in saved.items() if k in base}}

    @app.get("/api/team/settings")
    def get_team_settings() -> dict:
        return {"settings": team_settings(), "models": M.choices("claude")["models"], "efforts": M.EFFORTS}

    @app.patch("/api/team/settings")
    def patch_team_settings(body: TeamSettingsIn) -> dict:
        cur = team_settings()
        given = body.model_dump(exclude_unset=True)
        for role in ("master", "worker"):
            if f"{role}_effort" in given and given[f"{role}_effort"] in ("", "auto"):
                given[f"{role}_effort"] = None
            model = given.get(f"{role}_model", cur[f"{role}_model"])
            effort = given.get(f"{role}_effort", cur[f"{role}_effort"])
            try:
                M.check("claude", model, effort)
            except ValueError as e:
                raise HTTPException(400, f"{role}: {e}")
        if "max_workers" in given and (given["max_workers"] is None or int(given["max_workers"]) < 1):
            raise HTTPException(400, "workers at once must be 1 or more")
        if given.get("master_account"):
            _account(given["master_account"])
        cur.update(given)
        team_file.write_text(json.dumps(cur, indent=1), encoding="utf-8")
        return get_team_settings()

    def team_accounts() -> list[dict]:
        """Accounts a team may use right now: on, signed in, agent-capable, below their switch level."""
        out = []
        for a in accounts.list():
            if not a["enabled"] or a["status"] != "ok" or a["provider"] not in AGENT_PROVIDERS or not agent_ready(a):
                continue
            pct, _ = usage_pct(a)
            if pct is not None and pct >= a.get("switch_pct", DEFAULT_SWITCH):
                continue
            out.append(a)
        return out

    def team_deps(pid: str):
        from types import SimpleNamespace

        async def call(acc, sub, prompt, note, model, effort, role):
            files, voices, presets, jobs = await studio_state(pid)
            instructions = (STUDIO_INSTRUCTIONS + "\n\n" + actions.context(files, voices, presets, jobs)
                            + "\n\n" + note + "\n\n" + learning.instructions(pid, prompt))
            studio = Studio(pid, store, voice_svc, voice_jobs, tuned=tuner.for_voice, knowledge=knowledge, learning=learning, resolve=resolve, gates=gates, editor=editor, animation=animation,
                            on_job=lambda job: sub.emit({"t": "voicejob", "id": job["id"], "source": job["source"],
                                                         "voice": job["voice"]}))
            eff = M.effort_for(acc["provider"], model, effort, acc.get("codex_models"))
            try:
                res = await agent_for(acc).run(sub, runs, acc, prompt, None, instructions, studio, model,
                                               voice_gate, effort=eff)
            except asyncio.CancelledError:
                raise
            except Exception as e:                       # noqa: BLE001
                log.exception("team call failed")
                res = {"error": Fail("fatal", f"internal error: {e.__class__.__name__}"), "text": "",
                       "usage": None, "windows": None, "steps": []}
            err = res.get("error")
            if res.get("windows") and acc["provider"] in ("claude", "codex"):
                accounts.record_rate_limit(acc["id"], res["windows"], "allowed")
            if err and err.kind == "auth":
                accounts.mark_auth_error(acc["id"], err.message)
            if err and err.kind == "limit":
                pct, kind = usage_pct(accounts.get(acc["id"]))
                ledger.record_limit_hit(acc["id"], err.message, pct if kind == "estimated" else
                                        (100.0 if kind == "exact" else None))
            ledger.record_call(account=acc["id"], provider=acc["provider"], project=pid, usage=res.get("usage"),
                               outcome="error" if err else "ok", err_kind=err.kind if err else None,
                               windows=res.get("windows"), role=role)
            return res

        return SimpleNamespace(
            accounts=team_accounts, now=time.time, call=call,
            resets_at=lambda a: resets_of(accounts.get(a["id"])),
            over_cap=lambda a: over_cap(accounts.get(a["id"])),
            task_cap=lambda a: accounts.get(a["id"]).get("cap_task_tokens"),
            switch_allowed=switch_allowed, usage_pct=lambda a: usage_pct(a)[0],
            handoff_dir=lambda: store.project_dir(pid) / "handoffs", redact=redact)

    @app.post("/api/projects/{pid}/team")
    async def start_team(pid: str, body: TeamIn) -> StreamingResponse:
        _project(pid)
        parts = [p.model_dump() for p in body.tasks or [] if p.title.strip() or p.brief.strip()]
        goal = body.goal.strip() or ("Do the parts listed below." if parts else "")
        if not goal:
            raise HTTPException(400, "tell the team what to do")
        if not team_accounts():
            raise HTTPException(400, "team mode needs at least one signed-in Claude, Codex or API-key account "
                                     "that is below its switch level")
        cfg = TeamConfig.from_dict({**team_settings(), **(body.config or {})})
        try:
            run = runs.start(pid, store.files_dir(pid))
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        team = Team(pid, goal, run, team_deps(pid), cfg, state_file=store.project_dir(pid) / "team.json",
                    tasks=parts or None)
        teams[pid] = team
        said = goal + ("".join(f"\n{n}. {p['title'] or p['brief'][:60]}" for n, p in enumerate(parts, 1)))
        store.append_message(pid, "user", said, team=True)

        async def work():
            snap = await team.go()
            text = team.final or ("Team stopped." if snap["state"] == "stopped" else "")
            store.append_message(pid, "assistant", text, team={
                "id": snap["id"], "state": snap["state"], "master": snap["master"],
                "master_tokens": snap["master_tokens"],
                "tasks": [{k: t[k] for k in ("id", "title", "status", "account", "pins", "rounds", "tokens",
                                             "why", "handoffs")} for t in snap["tasks"]]})
            runs.finish(run)
            run.emit({"t": "done"})

        run.task = asyncio.get_running_loop().create_task(work())
        return StreamingResponse(follow(run), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})

    # ---------------------------------------------------------------- Script Studio (P10)
    script_file = settings.data_dir / "script_settings.json"
    ytdlp = ytdlp or competitors.YtDlp()

    def script_settings() -> dict:
        try:
            saved = json.loads(script_file.read_text(encoding="utf-8")) if script_file.exists() else {}
        except ValueError:
            saved = {}
        base = {k: getattr(ScriptConfig(), k) for k in ScriptConfig.__dataclass_fields__}
        out = {**base, **{k: v for k, v in saved.items() if k in base}}
        out["facelessos_dir"] = saved.get("facelessos_dir")
        return out

    @app.get("/api/script/settings")
    def get_script_settings() -> dict:
        s = script_settings()
        return {"settings": s, "facelessos": faceless.status(s["facelessos_dir"]),
                "formats": faceless.FORMATS, "models": M.choices("claude")["models"], "efforts": M.EFFORTS,
                "rubric": faceless.RUBRIC}

    @app.patch("/api/script/settings")
    def patch_script_settings(body: ScriptSettingsIn) -> dict:
        cur = script_settings()
        given = body.model_dump(exclude_unset=True)
        if "facelessos_dir" in given:
            d = (given["facelessos_dir"] or "").strip().strip('"')
            if d and not faceless.find(d):
                raise HTTPException(400, "no FacelessOS files in that folder (looking for faceless-scripts-os-master.md)")
            given["facelessos_dir"] = d or None
        for k, lo, hi in (("rounds", 1, 20), ("pass_score", 1, 10), ("stall", 1, 20), ("per_channel", 1, 20)):
            if k in given and (given[k] is None or not lo <= int(given[k]) <= hi):
                raise HTTPException(400, f"{k.replace('_', ' ')} must be {lo}-{hi}")
        for role in ("master", "worker"):
            if f"{role}_effort" in given and given[f"{role}_effort"] in ("", "auto"):
                given[f"{role}_effort"] = None
            try:
                M.check("claude", given.get(f"{role}_model", cur[f"{role}_model"]),
                        given.get(f"{role}_effort", cur[f"{role}_effort"]))
            except ValueError as e:
                raise HTTPException(400, f"{'reviewer' if role == 'master' else 'writer'}: {e}")
        if given.get("master_account"):
            _account(given["master_account"])
        cur.update(given)
        script_file.write_text(json.dumps(cur, indent=1), encoding="utf-8")
        return get_script_settings()

    @app.post("/api/projects/{pid}/script")
    async def start_script(pid: str, body: ScriptRunIn) -> StreamingResponse:
        _project(pid)
        topic = body.topic.strip()
        if not topic:
            raise HTTPException(400, "what is the video about?")
        s = script_settings()
        skills_dir = faceless.resolve(s["facelessos_dir"])
        if not skills_dir:
            raise HTTPException(400, "Script Studio needs FacelessOS files - set the folder in Script settings")
        text = body.links_text or ""
        if body.links_file:
            try:
                path = store.file_path(pid, body.links_file)
            except NotFound:
                raise HTTPException(404, "that competitor list isn't in this project")
            try:
                text += "\n" + competitors.read_list_file(path)
            except Exception as e:                       # noqa: BLE001
                raise HTTPException(400, f"couldn't read that file: {e.__class__.__name__}")
        links = competitors.find_links(text)
        if not links["items"]:
            raise HTTPException(400, "no YouTube links found in the competitor list")
        if not team_accounts():
            raise HTTPException(400, "Script Studio needs at least one signed-in Claude, Codex or API-key account")
        fmt = body.format if body.format in faceless.FORMATS else "documentary"
        brief = {"topic": topic[:200], "format": fmt, "minutes": max(1.0, min(180.0, float(body.minutes or 10))),
                 "niche": body.niche.strip()[:200], "notes": body.notes.strip()[:2000]}
        cfg = ScriptConfig.from_dict(s)
        skills = {role: faceless.load(skills_dir, role, fmt) for role in ("analyst", "writer", "reviewer")}
        try:
            run = runs.start(pid, store.files_dir(pid))
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        deps = team_deps(pid)
        dest = store.files_dir(pid) / "competitors"

        async def fetch(items, say):
            res = await competitors.collect(items, dest, ytdlp, per_channel=cfg.per_channel, scan=cfg.scan,
                                            progress=say)
            return {**res, "summary": competitors.summary(res["transcripts"])}

        deps.fetch = fetch
        sr = ScriptRun(pid, brief, links["items"], run, deps, cfg, skills, state_file=store.project_dir(pid) / "script.json")
        if links["skipped"]:
            sr.notes.append(f"{len(links['skipped'])} non-YouTube link(s) skipped")
        teams[pid] = sr
        store.append_message(pid, "user", f"Script: {brief['topic']} ({faceless.FORMATS[fmt]}, {brief['minutes']:g} min)"
                                          f" - {len(links['items'])} competitor link(s)", script=True)

        async def work():
            snap = await sr.go()
            r = snap["result"]
            if r.get("file"):
                text = (f"Script ready: {r['file']} - " if r.get("passed") else f"Best version kept: {r['file']} - ") + \
                       f"round {r['round']}, score {r['total']}/10, {r.get('words')} words. {r.get('why', '')}"
            else:
                text = "Script Studio stopped: " + (r.get("why") or snap["state"])
            store.append_message(pid, "assistant", text, script={
                "id": snap["id"], "state": snap["state"], "result": r, "rounds": [
                    {k: c[k] for k in ("round", "scores", "total", "must_fix", "passed", "words")} for c in snap["rounds"]],
                "fetch": {k: snap["fetch"].get(k) for k in ("done", "problems", "summary")}, "brief": snap["brief"],
                "master": snap["master"], "tasks": [{k: t[k] for k in ("id", "title", "status", "pins", "tokens", "handoffs")}
                                                    for t in snap["tasks"]]})
            runs.finish(run)
            run.emit({"t": "done"})

        run.task = asyncio.get_running_loop().create_task(work())
        return StreamingResponse(follow(run), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})

    @app.get("/api/projects/{pid}/script")
    def script_state(pid: str) -> dict:
        _project(pid)
        live = teams.get(pid)
        if isinstance(live, ScriptRun) and runs.busy(pid):
            return {"running": True, "script": live.snapshot()}
        f = store.project_dir(pid) / "script.json"
        return {"running": False, "script": json.loads(f.read_text(encoding="utf-8")) if f.exists() else None}

    @app.get("/api/projects/{pid}/team")
    def team_state(pid: str) -> dict:
        _project(pid)
        live = teams.get(pid)
        if live and runs.busy(pid):
            return {"running": True, "team": live.snapshot()}
        f = store.project_dir(pid) / "team.json"
        last = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        return {"running": False, "team": last}

    @app.post("/api/projects/{pid}/team/resume")
    def team_resume(pid: str) -> dict:
        _project(pid)
        live = teams.get(pid)
        if not live or not runs.busy(pid):
            raise HTTPException(404, "no team is working in this project")
        live.resume()
        return {"ok": True}

    # ---------------------------------------------------------------- handoff + switching (R3)
    @app.get("/api/projects/{pid}/handoffs")
    def list_handoffs(pid: str) -> list[dict]:
        _project(pid)
        folder = store.project_dir(pid) / "handoffs"
        if not folder.exists():
            return []
        return [{"name": f.name, "size": f.stat().st_size, "time": f.stat().st_mtime}
                for f in sorted(folder.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)]

    @app.get("/api/projects/{pid}/handoffs/{name}")
    def read_handoff(pid: str, name: str) -> PlainTextResponse:
        _project(pid)
        folder = (store.project_dir(pid) / "handoffs").resolve()
        path = (folder / Path(name).name).resolve()
        if path.parent != folder or not path.is_file():
            raise HTTPException(404, "handoff not found")
        return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")

    @app.post("/api/projects/{pid}/switch")
    def manual_switch(pid: str, body: SwitchIn) -> dict:
        """'Switch account...' button: write a handoff now and list who can take over."""
        _project(pid)
        acc = _account(body.account_id)
        path = write_handoff(pid, "manual", None if acc["id"] == "demo" else acc)
        pct, kind = usage_pct(acc)
        return {"t": "switch_offer", "reason": "manual", "from": acc["id"], "usage_pct": pct,
                "usage_kind": kind, "handoff": path.name, "from_resets_at": resets_of(acc),
                "eligible": eligible(acc)}

    @app.post("/api/projects/{pid}/continue")
    async def continue_on(pid: str, body: ContinueIn) -> StreamingResponse:
        """Hand the conversation to another account, which starts from the handoff file."""
        _project(pid)
        from_acc = _account(body.from_account)
        to_acc = _account(body.to_account)
        allowed = {e["id"]: e for e in eligible(from_acc)} if from_acc["id"] != "demo" else \
            {a["id"]: {"ok": a["enabled"] and a["status"] == "ok", "why": "not ready"} for a in accounts.list()}
        target = allowed.get(to_acc["id"])
        if not target or not target["ok"]:
            raise HTTPException(400, f"can't continue on that account: {(target or {}).get('why', 'unknown')}")
        folder = (store.project_dir(pid) / "handoffs").resolve()
        hpath = (folder / Path(body.handoff).name).resolve()
        if hpath.parent != folder or not hpath.is_file():
            raise HTTPException(404, "handoff not found")
        hist = store.history(pid)
        interrupted = bool(hist and hist[-1]["role"] == "assistant" and hist[-1].get("interrupted"))
        pct, _ = usage_pct(from_acc)
        ledger.record_switch(project=pid, from_acc=from_acc["id"], to_acc=to_acc["id"],
                             trigger=body.trigger, usage_at_switch=pct, handoff=hpath.name)
        store.append_message(pid, "system", f"Continued on {to_acc.get('label') or to_acc.get('email') or to_acc['id']} "
                                            f"from {hpath.name}", handoff=hpath.name, switch_to=to_acc["id"])
        prompt = SYSTEM + "\n\n" + handoff.continue_prompt(hpath.read_text(encoding="utf-8"), interrupted)
        return run_reply(pid, to_acc, prompt)

    # ---------------------------------------------------------------- voiceover (R5, R12)
    def _script_from(pid: str, body: ScriptIn) -> tuple[str, str]:
        if body.file:
            try:
                path = store.file_path(pid, body.file)
            except NotFound:
                raise HTTPException(404, "file not found")
            if path.suffix.lower() not in SCRIPT_TYPES:
                raise HTTPException(400, "scripts must be .docx, .txt or .md")
            try:
                return read_script(path), path.name
            except Exception as e:
                raise HTTPException(400, f"couldn't read that file: {e.__class__.__name__}")
        if body.text and body.text.strip():
            return body.text, "pasted text"
        raise HTTPException(400, "pick a script file or paste the script")

    @app.get("/api/voice/status")
    async def voice_status() -> dict:
        missing = voice_svc.missing()
        running = await voice_svc.running()
        meta = {}
        if running:
            try:
                meta = await voice_svc.meta()
            except Exception:
                meta = {}
        return {"configured": not missing, "missing": missing, "running": running,
                "voices": meta.get("voices", []), "presets": meta.get("presets", {}),
                "approved": approvals.always("voiceover"), "tool": TOOLS["voiceover"],
                "tuned": {k: v.get("best") for k, v in tuner.tuned().items()}}

    @app.get("/api/voice/sample/{name}")
    async def voice_sample(name: str) -> FileResponse:
        """The reference clip itself, so the user can hear a voice before choosing it."""
        try:
            meta = await voice_svc.meta()
        except Exception:
            raise HTTPException(503, "the voice engine isn't running")
        folder = Path(meta.get("voices_dir") or ".").resolve()
        path = (folder / name).resolve()
        if name not in meta.get("voices", []) or path.parent != folder or not path.is_file():
            raise HTTPException(404, "no such voice")
        return FileResponse(path, media_type="audio/wav")

    @app.post("/api/voice/clone")
    async def voice_clone(name: str = Form(...), consent: bool = Form(False),
                          file: UploadFile | None = File(None),
                          project: str | None = Form(None), project_file: str | None = Form(None)) -> dict:
        """Add a new voice: a 10-30 s clip from the PC or from a project's files."""
        if not consent:
            raise HTTPException(400, "only clone your own voice or one you have permission to use")
        safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_ ").strip()
        if not safe:
            raise HTTPException(400, "give the voice a name (letters, numbers, - and _)")
        if file is not None:
            src_name, data = file.filename or "clip.wav", await file.read(MAX_CLIP + 1)
        elif project and project_file:
            _project(project)
            try:
                path = store.file_path(project, project_file)
            except NotFound:
                raise HTTPException(404, "file not found")
            src_name, data = path.name, path.read_bytes()[:MAX_CLIP + 1]
        else:
            raise HTTPException(400, "choose a clip")
        ext = Path(src_name).suffix.lower()
        if ext not in (".wav", ".mp3", ".flac"):
            raise HTTPException(400, "reference clips must be .wav, .mp3 or .flac")
        if len(data) > MAX_CLIP:
            raise HTTPException(400, "that clip is too big - 10 to 30 seconds is all a clone needs")
        try:
            await voice_svc.ensure_running()
            if f"{safe}_ref48.wav" in (await voice_svc.meta()).get("voices", []):
                raise HTTPException(409, f"there is already a voice called {safe} - pick another name")
            return await voice_svc.add_voice(safe + ext, data)
        except VoiceError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/voice/start")
    async def voice_start() -> dict:
        try:
            await voice_svc.ensure_running()
        except VoiceError as e:
            raise HTTPException(400, str(e))
        return await voice_status()

    @app.post("/api/projects/{pid}/script-stats")
    def script_info(pid: str, body: ScriptIn) -> dict:
        _project(pid)
        text, source = _script_from(pid, body)
        return {"source": source, **script_stats(text)}

    @app.post("/api/projects/{pid}/voice")
    async def start_voice(pid: str, body: VoiceIn):
        _project(pid)
        text, source = _script_from(pid, body)
        need = approvals.check("voiceover", body.approve)
        if need:
            from fastapi.responses import JSONResponse
            return JSONResponse({"needs_approval": need}, status_code=409)
        if body.pace_mode not in ("natural", "wpm", "grid", "grid_pad"):
            raise HTTPException(400, "unknown pace mode")
        if body.pace_mode == "wpm" and not body.target_wpm:
            raise HTTPException(400, "set the words per minute")
        name = (body.name or (Path(body.file).stem if body.file else "voiceover")).strip()[:80] or "voiceover"
        name = "".join(c for c in name if c.isalnum() or c in "-_ ").strip() or "voiceover"
        tuned = tuner.for_voice(body.voice) or {}
        spec = {"voice": body.voice, "name": name, "preset": body.preset, "pace_mode": body.pace_mode,
                "target_wpm": body.target_wpm, "grid_gap": body.grid_gap, "auto_fix": True,
                "exaggeration": body.exaggeration if body.exaggeration is not None else tuned.get("exaggeration"),
                "cfg_weight": body.cfg_weight if body.cfg_weight is not None else tuned.get("cfg_weight"),
                "temperature": body.temperature if body.temperature is not None else tuned.get("temperature")}
        job = voice_jobs.start(pid, text, spec, source)
        extra = {"from_action": body.from_action} if body.from_action else {}
        store.append_message(pid, "user", f"Voice {source} with {body.voice}"
                             + (f" at {int(body.target_wpm)} wpm" if body.pace_mode == "wpm" else ""),
                             tool_request="voiceover", job=job["id"], **extra)
        return job

    @app.get("/api/voicejobs/{jid}")
    def voice_job(jid: str) -> dict:
        job = voice_jobs.get(jid)
        if not job:
            raise HTTPException(404, "job not found")
        return job

    @app.get("/api/approvals")
    def list_approvals() -> dict:
        return {t: approvals.always(t) for t in TOOLS}

    @app.delete("/api/approvals/{tool}")
    def revoke_approval(tool: str) -> dict:
        if tool not in TOOLS:
            raise HTTPException(404, "unknown tool")
        approvals.set_always(tool, False)
        return {t: approvals.always(t) for t in TOOLS}

    return app
