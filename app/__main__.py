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
from .security.redact import RedactingFilter
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
    handlers = [logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler(sys.stdout)]
    for h in handlers:
        h.addFilter(RedactingFilter())
    logging.basicConfig(level=settings.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    log = logging.getLogger("oneprod")

    port = pick_port(settings.port)
    url = f"http://{LOOPBACK}:{port}/"
    app = create_app(settings)
    # The token travels only in the URL fragment, which browsers never send to the server,
    # and the page removes it from the address bar and history on load.
    launch_url = f"{url}#t={app.state.token}"

    log.info("One Production %s starting on %s (data: %s)", __version__, url, settings.data_dir)
    print(f"\n  One Production {__version__}\n  {url}\n  data: {settings.data_dir}\n"
          f"  Close this window to stop.\n", flush=True)

    if settings.open_browser:
        threading.Timer(1.0, webbrowser.open, args=(launch_url,)).start()
    else:
        # Headless/test runs: the launch link goes to the console only, never to the log file.
        print(f"  launch: {launch_url}\n", flush=True)

    uvicorn.run(app, host=LOOPBACK, port=port, log_level=settings.log_level, log_config=None)


if __name__ == "__main__":
    main()
