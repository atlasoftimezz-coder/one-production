"""HTTP application.

Public (no token): /health and /  - the page itself holds no secret.
Everything under /api/ needs the session token (see security/guard.py).
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import secrets
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from . import handoff
from .accounts import DEFAULT_SWITCH, DEFAULT_WARN, PROVIDERS, SAME_SERVICE_SWITCHING, SUBSCRIPTIONS, \
    AccountError, Accounts
from .chat import SYSTEM, build_prompt, ndjson, reply_events
from .config import Settings
from .providers.anthropic_api import AnthropicAPI
from .providers.base import Fail, RateLimit, Text, Usage
from .providers.claude_cli import ClaudeCLI
from .providers.codex_cli import CodexCLI
from .providers.demo import Demo
from .security.guard import GuardMiddleware
from .security.redact import redact, register_secret
from .storage import NotFound, Store
from .usage import RANGES, Ledger, range_start
from .version import __version__

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
UPLOAD_CHUNK = 1024 * 1024
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
    warn_pct: int | None = None
    switch_pct: int | None = None
    budget_tokens_5h: int | None = -1          # -1 = leave unchanged, null = clear


class OrderIn(BaseModel):
    ids: list[str]


def create_app(settings: Settings, token: str | None = None, *, api_transport=None,
               login_console: bool | None = None) -> FastAPI:
    if login_console is None:
        login_console = settings.login_console
    token = token or secrets.token_urlsafe(32)
    register_secret(token)
    store = Store(settings.data_dir)
    accounts = Accounts(settings.data_dir)
    ledger = Ledger(settings.data_dir)
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

    @asynccontextmanager
    async def lifespan(_app):
        yield
        shutdown_handoffs()

    app = FastAPI(title="One Production", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GuardMiddleware, token=token)
    app.state.token = token          # read by the launcher only; never sent in a response

    def _project(pid: str) -> None:
        try:
            store.files_dir(pid)
        except NotFound:
            raise HTTPException(404, "project not found")

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
        windows = [w.get("utilization") for w in (rl.get("windows") or {}).values()
                   if w.get("utilization") is not None]
        if windows and time.time() - rl.get("at", 0) < 5 * 3600:
            return round(max(windows) * 100, 1), "exact"
        budget = acc.get("budget_tokens_5h")
        if budget:
            used = ledger.tokens_since(acc["id"], time.time() - 5 * 3600)
            return round(100 * used / budget, 1), "estimated"
        return None, "none"

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
            elif (not SAME_SERVICE_SWITCHING and a["provider"] in SUBSCRIPTIONS
                  and a["provider"] == from_acc["provider"]):
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
    def run_reply(pid: str, acc: dict, prompt: str):
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
                                                   on_ratelimit, on_auth):
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
            if text or error:
                store.append_message(pid, "assistant", text, **meta)
            ledger.record_call(account=acc["id"], provider=acc["provider"], project=pid, usage=usage,
                               outcome="error" if error else "ok", err_kind=error.kind if error else None,
                               windows=state["windows"])

            # ---- switch / backup decisions (plan R3, D6)
            if acc["id"] != "demo":
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
                    yield ndjson({"t": "switch_offer", "reason": reason, "from": acc["id"],
                                  "usage_pct": pct, "usage_kind": kind, "handoff": path.name,
                                  "from_resets_at": resets_of(fresh), "eligible": eligible(fresh)})
                else:
                    replies = sum(1 for m in store.history(pid) if m["role"] == "assistant")
                    if replies and replies % handoff.BACKUP_EVERY == 0:
                        write_handoff(pid, "backup", fresh)
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

    @app.get("/api/projects/{pid}/files/{name}")
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
            if body.warn_pct is not None or body.switch_pct is not None or body.budget_tokens_5h != -1:
                accounts.set_limits(acc_id, body.warn_pct, body.switch_pct, body.budget_tokens_5h)
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
                          "accuracy": ledger.estimate_accuracy(a["id"]) if kind == "estimated" else None})
        return {"range": range, "since": s["since"], "cards": cards, "totals": s["totals"],
                "by_project": s["by_project"], "by_model": s["by_model"],
                "same_service_switching": SAME_SERVICE_SWITCHING}

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
        return PlainTextResponse(ledger.export_csv(range, redact), media_type="text/csv; charset=utf-8",
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
        prompt = build_prompt(store.history(pid), body.message)
        store.append_message(pid, "user", body.message)
        return run_reply(pid, acc, prompt)

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

    return app
