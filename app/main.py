"""HTTP application. P0 serves only the health check and the start page."""
from __future__ import annotations

import platform
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from .config import Settings
from .version import __version__

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="One Production", version=__version__, docs_url=None, redoc_url=None)

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "data_dir": str(settings.data_dir),
            "python": platform.python_version(),
        }

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    return app
