"""HTTP application.

Public (no token): /health and /  - the page itself holds no secret.
Everything under /api/ needs the session token (see security/guard.py).
"""
from __future__ import annotations

import platform
import secrets
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .chat import stub_reply
from .config import Settings
from .security.guard import GuardMiddleware
from .security.redact import register_secret
from .storage import NotFound, Store
from .version import __version__

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
UPLOAD_CHUNK = 1024 * 1024


class NewProject(BaseModel):
    name: str = ""


class ChatIn(BaseModel):
    message: str


def create_app(settings: Settings, token: str | None = None) -> FastAPI:
    token = token or secrets.token_urlsafe(32)
    register_secret(token)
    store = Store(settings.data_dir)

    app = FastAPI(title="One Production", version=__version__,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GuardMiddleware, token=token)
    app.state.token = token          # read by the launcher only; never sent in a response

    def _project(pid: str) -> None:
        try:
            store.files_dir(pid)
        except NotFound:
            raise HTTPException(404, "project not found")

    # ---- public
    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__,
                "data_dir": str(settings.data_dir), "python": platform.python_version()}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html", media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "no-store"})

    # ---- projects
    @app.get("/api/projects")
    def list_projects() -> list[dict]:
        return store.list_projects()

    @app.post("/api/projects")
    def create_project(body: NewProject) -> dict:
        return store.create_project(body.name)

    # ---- files (any type)
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

    # ---- chat (stub replies until P2 connects real accounts)
    @app.get("/api/projects/{pid}/chat")
    def history(pid: str) -> list[dict]:
        _project(pid)
        return store.history(pid)

    @app.post("/api/projects/{pid}/chat")
    async def chat(pid: str, body: ChatIn) -> StreamingResponse:
        _project(pid)
        store.append_message(pid, "user", body.message)

        async def stream():
            parts = []
            async for piece in stub_reply(body.message):
                parts.append(piece)
                yield piece
            store.append_message(pid, "assistant", "".join(parts).strip())

        return StreamingResponse(stream(), media_type="text/plain; charset=utf-8",
                                 headers={"Cache-Control": "no-store"})

    return app
