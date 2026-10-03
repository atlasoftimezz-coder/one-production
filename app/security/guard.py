"""Request guard for a server that will later run shell-like tools.

Binding to 127.0.0.1 is not enough: any web page you open can still send requests to
localhost (cross-origin), and DNS rebinding can make a foreign hostname resolve to
127.0.0.1. So every request must pass three checks:

1. Host header is 127.0.0.1 or localhost      -> blocks DNS rebinding
2. Origin, if present, is this app itself      -> blocks other web pages
3. /api/* carries the session token            -> only the page the launcher opened

The token is generated at startup and handed to the page in the URL fragment (#t=...),
which browsers never send to servers. No endpoint ever returns it.
"""
from __future__ import annotations

import hmac
import logging
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

TOKEN_HEADER = "X-OneProd-Token"
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost"}

log = logging.getLogger("oneprod.security")


def _hostname(host_header: str) -> str:
    # "127.0.0.1:8730" -> "127.0.0.1"; "[::1]:8730" -> "::1"
    return urlsplit(f"//{host_header}").hostname or ""


class GuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str):
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "")
        if _hostname(host) not in LOCAL_HOSTNAMES:
            log.warning("rejected: bad Host %r for %s", host, request.url.path)
            return JSONResponse({"error": "bad host"}, status_code=400)

        origin = request.headers.get("origin")
        if origin is not None and _hostname(urlsplit(origin).netloc) not in LOCAL_HOSTNAMES:
            log.warning("rejected: foreign Origin %r for %s", origin, request.url.path)
            return JSONResponse({"error": "foreign origin"}, status_code=403)

        if request.url.path.startswith("/api/"):
            sent = request.headers.get(TOKEN_HEADER, "")
            if not sent or not hmac.compare_digest(sent, self._token):
                log.warning("rejected: missing/invalid token for %s", request.url.path)
                return JSONResponse({"error": "not authorised - open the app from start.bat"},
                                    status_code=401)
        return await call_next(request)
