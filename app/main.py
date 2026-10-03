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
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .accounts import PROVIDERS, AccountError, Accounts
from .chat import build_prompt, reply_events
from .config import Settings
from .providers.anthropic_api import AnthropicAPI
from .providers.base import Text, Usage
from .providers.claude_cli import ClaudeCLI
from .providers.codex_cli import CodexCLI
from .providers.demo import Demo
from .security.guard import GuardMiddleware
from .security.redact import register_secret
from .storage import NotFound, Store
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


class NewAccount(BaseModel):
    provider: str
    label: str = ""
    api_key: str | None = None
    model: str | None = None


class AccountPatch(BaseModel):
    label: str | None = None
    enabled: bool | None = None
    model: str | None = None


def create_app(settings: Settings, token: str | None = None, *, api_transport=None,
               login_console: bool | None = None) -> FastAPI:
    if login_console is None:
        login_console = settings.login_console
    token = token or secrets.token_urlsafe(32)
    register_secret(token)
    store = Store(settings.data_dir)
    accounts = Accounts(settings.data_dir)
    work = settings.data_dir / "cache" / "run"          # empty cwd: CLIs pick up no project files
    providers = {
        "demo": Demo(),
        "claude": ClaudeCLI(settings.claude_cmd, work, settings.reply_timeout),
        "codex": CodexCLI(settings.codex_cmd, work, settings.reply_timeout),
        "anthropic_api": AnthropicAPI(Accounts.api_key, settings.anthropic_base_url, api_transport,
                                      settings.reply_timeout),
    }

    app = FastAPI(title="One Production", version=__version__,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GuardMiddleware, token=token)
    app.state.token = token          # read by the launcher only; never sent in a response

    def _project(pid: str) -> None:
        try:
            store.files_dir(pid)
        except NotFound:
            raise HTTPException(404, "project not found")

    def _account(acc_id: str) -> dict:
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

    @app.get("/api/accounts")
    def list_accounts() -> list[dict]:
        return [DEMO_ACCOUNT] + [Accounts.public(a) for a in accounts.list()]

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
        return Accounts.public(acc)

    @app.post("/api/accounts/check")
    async def check_all() -> list[dict]:
        accs = [a for a in accounts.list() if a["enabled"]]
        await asyncio.gather(*(_check(a) for a in accs))
        return list_accounts()

    @app.post("/api/accounts/{acc_id}/check")
    async def check_one(acc_id: str) -> dict:
        return Accounts.public(await _check(_account(acc_id)))

    @app.post("/api/accounts/{acc_id}/login")
    def open_login(acc_id: str) -> dict:
        """Open the provider's own login in a separate window, bound to this account's folder.
        The user signs in there; the page then polls /check."""
        acc = _account(acc_id)
        prov = providers[acc["provider"]]
        argv = getattr(prov, "login_argv", lambda: None)()
        if not argv:
            raise HTTPException(400, f"{PROVIDERS[acc['provider']]['label']} login is not available on this PC")
        env = {**os.environ, **prov.env(acc)}
        flags = subprocess.CREATE_NEW_CONSOLE if login_console else subprocess.CREATE_NO_WINDOW
        work.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(argv, env=env, cwd=str(work), creationflags=flags,
                         stdin=None if login_console else subprocess.DEVNULL)
        return {"started": True}

    @app.patch("/api/accounts/{acc_id}")
    def patch_account(acc_id: str, body: AccountPatch) -> dict:
        _account(acc_id)
        try:
            if body.label is not None:
                accounts.rename(acc_id, body.label)
            if body.enabled is not None:
                accounts.set_enabled(acc_id, body.enabled)
            if body.model is not None:
                accounts.set_model(acc_id, body.model)
        except AccountError as e:
            raise HTTPException(400, str(e))
        return Accounts.public(accounts.get(acc_id))

    @app.delete("/api/accounts/{acc_id}")
    def delete_account(acc_id: str) -> dict:
        _account(acc_id)
        accounts.remove(acc_id)
        return {"removed": acc_id}

    # ---------------------------------------------------------------- chat
    @app.get("/api/projects/{pid}/chat")
    def history(pid: str) -> list[dict]:
        _project(pid)
        return store.history(pid)

    @app.post("/api/projects/{pid}/chat")
    async def chat(pid: str, body: ChatIn) -> StreamingResponse:
        _project(pid)
        if not body.account_id or body.account_id == "demo":
            acc = DEMO_ACCOUNT
        else:
            acc = _account(body.account_id)
            if not acc["enabled"]:
                raise HTTPException(400, "that account is turned off")
        prompt = build_prompt(store.history(pid), body.message)
        store.append_message(pid, "user", body.message)
        provider = providers[acc["provider"]]

        def on_ratelimit(ev):
            if acc["id"] != "demo":
                accounts.record_rate_limit(acc["id"], ev.windows, ev.status)

        def on_auth(ev):
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
                    elif ev is not None and getattr(ev, "kind", None):
                        error = ev
                    yield line
            except Exception as e:      # never leave the browser hanging on a broken stream
                log.exception("chat stream failed")
                from .providers.base import Fail
                error = Fail("fatal", f"internal error: {e.__class__.__name__}", interrupted=bool(parts))
                from .chat import ndjson
                yield ndjson({"t": "error", "kind": "fatal", "message": error.message,
                              "interrupted": error.interrupted})
                yield ndjson({"t": "done"})
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

        return StreamingResponse(stream(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store"})

    return app
