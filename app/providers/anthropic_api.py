"""Anthropic Messages API with the account's own API key (kept in Windows Credential Manager).

Usage is exact: every response reports its tokens."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from ..models import API_IDS
from .base import Event, Fail, Text, Usage, classify_status

DEFAULT_BASE = "https://api.anthropic.com"
VERSION = "2023-06-01"
MODELS = API_IDS                    # opus / sonnet / haiku / fable -> API model id


class AnthropicAPI:
    name = "anthropic_api"

    def __init__(self, get_key, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 300):
        self.get_key = get_key           # account -> key, read from the key store at call time
        self.base = (base_url or DEFAULT_BASE).rstrip("/")
        self.transport = transport       # tests inject a fake transport
        self.timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport, timeout=httpx.Timeout(self.timeout, connect=15))

    @staticmethod
    def _headers(key: str) -> dict:
        return {"x-api-key": key, "anthropic-version": VERSION, "content-type": "application/json"}

    async def check_key(self, key: str) -> dict:
        """Listing models is free, so this validates a key without spending anything."""
        try:
            async with self._client() as c:
                r = await c.get(f"{self.base}/v1/models", headers=self._headers(key))
        except httpx.HTTPError as e:
            return {"ok": False, "reason": f"could not reach Anthropic: {e.__class__.__name__}"}
        if r.status_code == 200:
            return {"ok": True}
        kind = classify_status(r.status_code)
        return {"ok": False, "reason": "key rejected" if kind == "auth" else f"HTTP {r.status_code}"}

    async def status(self, account: dict) -> dict:
        key = self.get_key(account)
        if not key:
            return {"ok": False, "reason": "no key stored"}
        res = await self.check_key(key)
        if res["ok"]:
            res.update(method="API key")
        return res

    async def stream(self, account: dict, prompt: str, model: str | None,
                     effort: str | None = None) -> AsyncIterator[Event]:
        key = self.get_key(account)
        if not key:
            yield Fail("auth", "no API key stored for this account")
            return
        # current models think adaptively by default; effort sets how deep (output_config.effort)
        body = {"model": MODELS.get(model or "sonnet", MODELS["sonnet"]), "max_tokens": 16000,
                "stream": True, "messages": [{"role": "user", "content": prompt}]}
        if effort:
            body["output_config"] = {"effort": effort}
        usage = Usage(model=body["model"])
        try:
            async with self._client() as c:
                async with c.stream("POST", f"{self.base}/v1/messages", headers=self._headers(key),
                                    json=body) as r:
                    if r.status_code != 200:
                        text = (await r.aread()).decode("utf-8", "replace")[:300]
                        yield Fail(classify_status(r.status_code), f"HTTP {r.status_code}: {text}")
                        return
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            d = json.loads(line[5:].strip())
                        except ValueError:
                            continue
                        t = d.get("type")
                        if t == "content_block_delta" and (d.get("delta") or {}).get("type") == "text_delta":
                            yield Text(d["delta"].get("text", ""))
                        elif t == "message_start":
                            u = (d.get("message") or {}).get("usage") or {}
                            usage.input_tokens = u.get("input_tokens", 0)
                            usage.cache_read_tokens = u.get("cache_read_input_tokens", 0) or 0
                            usage.cache_write_tokens = u.get("cache_creation_input_tokens", 0) or 0
                        elif t == "message_delta":
                            usage.output_tokens = (d.get("usage") or {}).get("output_tokens", usage.output_tokens)
                        elif t == "error":
                            err = d.get("error") or {}
                            etype = err.get("type", "")
                            kind = ("limit" if "rate_limit" in etype else
                                    "transient" if etype in ("overloaded_error", "api_error") else "fatal")
                            yield usage
                            yield Fail(kind, err.get("message", etype)[:300])
                            return
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            yield usage
            yield Fail("transient", f"{e.__class__.__name__}: network problem talking to Anthropic")
            return
        yield usage
