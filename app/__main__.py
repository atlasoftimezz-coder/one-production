"""Launcher: `python -m app` (start.bat calls this)."""
from __future__ import annotations

import logging
import socket
import sys
import threading
import webbrowser

import uvicorn

from .config import LOOPBACK, load_settings
from .main import create_app
from .version import __version__

DEFAULT_PORT = 8730
PORT_SEARCH = 50


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((LOOPBACK, port))
        except OSError:
            return False
        return True


def pick_port(preferred: int) -> int:
    if preferred:
        if port_is_free(preferred):
            return preferred
        raise SystemExit(f"Port {preferred} is in use. Close the other program or remove the port setting.")
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SEARCH):
        if port_is_free(port):
            return port
    raise SystemExit(f"No free port between {DEFAULT_PORT} and {DEFAULT_PORT + PORT_SEARCH - 1}.")


def main() -> None:
    settings = load_settings()
    settings.ensure_dirs()
    log_file = settings.data_dir / "logs" / "app.log"
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger("oneprod")
    port = pick_port(settings.port)
    url = f"http://{LOOPBACK}:{port}/"
    log.info("One Production %s starting on %s (data: %s)", __version__, url, settings.data_dir)
    print(f"\n  One Production {__version__}\n  {url}\n  data: {settings.data_dir}\n  Close this window to stop.\n")

    if settings.open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()

    uvicorn.run(create_app(settings), host=LOOPBACK, port=port,
                log_level=settings.log_level, log_config=None)


if __name__ == "__main__":
    main()
