"""Shared MCP client and browser OAuth. Credentials stay in Windows Credential Manager.

Connections are created within one async task and closed there. This avoids sharing an
SDK task group across request lifetimes. No account request happens while listing status.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import threading
import time
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import keyring

from .accounts import KEYRING_SERVICE
from . import mcp_servers
from .security.redact import redact, register_secret


def namespace(name, url):
    return f"source-oauth:{name}:{hashlib.sha256(url.encode()).hexdigest()[:20]}"


class CredentialStorage:
    def __init__(self, name, url, guard=None, lock=None):
        self.key = namespace(name, url)
        self.guard, self.lock = guard, lock or threading.RLock()

    def read(self, suffix):
        value = keyring.get_password(KEYRING_SERVICE, self.key + suffix)
        return json.loads(value) if value else None

    def write(self, suffix, value):
        with self.lock:
            if self.guard and not self.guard():
                raise ValueError('This source connection changed while signing in. Start again.')
            for name in ("access_token", "refresh_token", "client_secret"):
                register_secret(value.get(name, ""))
            keyring.set_password(KEYRING_SERVICE, self.key + suffix, json.dumps(value))

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        value = self.read(":tokens")
        if value:
            expiry = value.pop("_expires_at", None)
            if expiry is not None:
                value["expires_in"] = max(0, int(expiry - time.time()))
            return OAuthToken.model_validate(value)

    async def set_tokens(self, tokens):
        value = tokens.model_dump(mode="json", exclude_none=True)
        if tokens.expires_in is not None:
            value["_expires_at"] = time.time() + tokens.expires_in
        self.write(":tokens", value)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        value = self.read(":client")
        return OAuthClientInformationFull.model_validate(value) if value else None

    async def set_client_info(self, info):
        self.write(":client", info.model_dump(mode="json", exclude_none=True))

    def forget(self):
        for suffix in (":tokens", ":client"):
            try:
                keyring.delete_password(KEYRING_SERVICE, self.key + suffix)
            except keyring.errors.PasswordDeleteError:
                pass


class OAuthLoopback:
    def __init__(self):
        self.state, self.result, self.error = secrets.token_urlsafe(32), None, None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                q = parse_qs(urlparse(self.path).query)
                valid = q.get("state", [""])[0] == owner.state
                if valid:
                    if q.get("code"):
                        owner.result = {"code": q["code"][0], "state": owner.state, "iss": q.get("iss", [None])[0]}
                    else:
                        owner.error = "Sign-in was declined or no authorization code was returned."
                self.send_response(200 if valid else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b"<!doctype html><meta charset=utf-8><title>One Production</title><p>Return to One Production to check the connection.</p>" if valid else b"Invalid sign-in state.")

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/callback"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def error_text(exc):
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return redact(str(exc))[:500] or type(exc).__name__


class SourceMcp:
    def __init__(self, settings, client_factory=None):
        self.settings, self.factory = settings, client_factory
        self.jobs, self.tasks = {}, {}
        self.generations, self.auth_lock, self.running = {}, threading.RLock(), set()

    def config(self, name):
        servers = mcp_servers.check(self.settings.mcp_servers or {})
        if name not in servers or not servers[name].get("enabled", True):
            raise ValueError(f"Source {name!r} is missing or disabled. Open MCP to connect it.")
        return servers[name]

    @asynccontextmanager
    async def client(self, name, login=None):
        cfg = self.config(name)
        if self.factory:
            async with self.factory(name, cfg, login) as client:
                yield client
            return
        import httpx2
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters
        from mcp.client.streamable_http import streamable_http_client
        from mcp.client.sse import sse_client
        if cfg["type"] == "stdio":
            params = StdioServerParameters(command=cfg["command"], args=cfg.get("args", []), env=cfg.get("env") or None)
            async with Client(params, mode="legacy", read_timeout_seconds=90) as client:
                yield client
            return
        headers = mcp_servers.headers_of(name, cfg)
        auth = None
        if cfg.get("auth") == "oauth":
            from mcp.client.auth import OAuthClientProvider
            from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata
            with self.auth_lock:
                generation = self.generations.get(name, 0)
            storage = CredentialStorage(name, cfg["url"],
                guard=lambda: self.generations.get(name, 0) == generation, lock=self.auth_lock)
            info = await storage.get_client_info()
            redirect = login["listener"].url if login else str(info.redirect_uris[0]) if info and info.redirect_uris else "http://127.0.0.1/callback"

            async def redirected(url):
                if not login:
                    raise ValueError("Browser sign-in is required. Open MCP and press Sign in for this source.")
                parsed = urlparse(url)
                if parsed.scheme != "https" and parsed.hostname not in ("127.0.0.1", "localhost"):
                    raise ValueError("The source returned an insecure authorization URL.")
                login["listener"].state = parse_qs(parsed.query).get("state", [""])[0]
                if not login["listener"].state:
                    raise ValueError("The source returned no OAuth state.")
                login.update(state="waiting", url=url)

            async def callback():
                listener = login["listener"]
                while not listener.result:
                    if listener.error:
                        raise ValueError(listener.error)
                    await asyncio.sleep(.2)
                return AuthorizationCodeResult(**listener.result)

            auth = OAuthClientProvider(cfg["url"], OAuthClientMetadata(
                client_name="One Production", redirect_uris=[redirect],
                grant_types=["authorization_code", "refresh_token"], response_types=["code"],
                token_endpoint_auth_method="none"), storage, redirected, callback if login else None)
        if cfg["type"] == "sse":
            async with Client(sse_client(cfg["url"], headers=headers), mode="legacy", read_timeout_seconds=90) as client:
                yield client
        else:
            async with httpx2.AsyncClient(headers=headers, auth=auth, timeout=90) as http:
                async with Client(streamable_http_client(cfg["url"], http_client=http), mode="legacy", read_timeout_seconds=90) as client:
                    yield client

    async def tools(self, name):
        result, cursor = [], None
        async with asyncio.timeout(90):
            async with self.client(name) as client:
                for _ in range(20):
                    page = await client.list_tools(cursor=cursor)
                    result.extend(t.model_dump(mode="json", by_alias=True) for t in page.tools)
                    cursor = page.next_cursor
                    if not cursor:
                        return result
        raise ValueError("This source returned too many tool pages.")

    async def call(self, name, tool, arguments):
        async with asyncio.timeout(120):
            async with self.client(name) as client:
                result = await client.call_tool(tool, arguments)
                data = result.model_dump(mode="json", by_alias=True, exclude_none=True)
                if data.get("isError"):
                    raise ValueError("; ".join(c.get("text", "Source call failed") for c in data.get("content", [])))
                return data

    def public_job(self, job):
        return {k: v for k, v in job.items() if k not in ("listener", "url_snapshot")}

    def start_login(self, name):
        cfg = self.config(name)
        if cfg.get("auth") != "oauth":
            raise ValueError("Choose browser sign-in before connecting this source.")
        self.forget(name, cfg)
        job = {"id": secrets.token_hex(12), "source": name, "state": "starting", "url_snapshot": cfg["url"], "listener": OAuthLoopback()}
        self.jobs[job["id"]] = job

        async def work():
            try:
                async with asyncio.timeout(300):
                    async with self.client(name, job) as client:
                        tools = await client.list_tools()
                        job.update(state="connected", tools=[t.name for t in tools.tools])
            except asyncio.CancelledError:
                job.update(state="cancelled", url="")
                raise
            except Exception as exc:
                job.update(state="failed", error=error_text(exc), url="")
            finally:
                await asyncio.to_thread(job["listener"].close)
                job.pop("url", None)

        self.tasks[name] = asyncio.create_task(work())
        self.running.add(self.tasks[name])
        self.tasks[name].add_done_callback(self.running.discard)
        return self.public_job(job)

    def cancel(self, name):
        with self.auth_lock:
            self.generations[name] = self.generations.get(name, 0) + 1
        task = self.tasks.pop(name, None)
        if task and not task.done():
            task.get_loop().call_soon_threadsafe(task.cancel)

    def forget(self, name, cfg):
        self.cancel(name)
        if cfg.get("url"):
            with self.auth_lock:
                CredentialStorage(name, cfg["url"]).forget()

    async def close(self):
        tasks = list(self.running)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
