"""Shared event types and error classification for every provider.

A provider's stream yields Text, Usage and RateLimit events and ends either normally or
with exactly one Fail. Fail.kind decides what happens next (plan R9):

    limit      usage / rate limit reached       -> stop; P3 hands off to the next account
    transient  network, timeout, 5xx, overload  -> retry, but only if no text arrived yet
    auth       not logged in, bad key           -> mark the account red, ask to re-login
    fatal      bad request, anything else       -> show the error, no retry
"""
from __future__ import annotations

import asyncio
import os
import re
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol


@dataclass
class Text:
    text: str


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None      # API-equivalent cost reported by the client, if any
    model: str | None = None


@dataclass
class RateLimit:
    """Utilisation reported by the provider itself (Claude CLI does this; exact, not estimated)."""
    windows: dict[str, dict[str, Any]] = field(default_factory=dict)   # name -> {utilization, resets_at}
    status: str = "allowed"


@dataclass
class Fail:
    kind: str           # limit | transient | auth | fatal
    message: str
    interrupted: bool = False   # set by the runner when text had already been streamed


Event = Text | Usage | RateLimit | Fail


class Provider(Protocol):
    name: str

    def stream(self, account: dict, prompt: str, model: str | None) -> AsyncIterator[Event]: ...


# ---------------------------------------------------------------- classification

_LIMIT = re.compile(r"rate.?limit|usage.?limit|quota|too many requests|\b429\b|limit reached|"
                    r"hit your limit|exceeded", re.I)
_AUTH = re.compile(r"not logged in|log ?in|login required|unauthori[sz]ed|invalid.{0,10}(api.?key|x-api-key|token)|"
                   r"authentication|\b401\b|\b403\b|forbidden|credentials", re.I)
_TRANSIENT = re.compile(r"overloaded|\b5\d\d\b|timed? ?out|timeout|connection|network|temporar|"
                        r"unavailable|ECONNRESET|ENOTFOUND|socket", re.I)


def classify_status(status: int | None) -> str:
    if status is None:
        return "fatal"
    if status == 429:
        return "limit"
    if status in (401, 403):
        return "auth"
    if status in (408, 409) or status >= 500:
        return "transient"
    return "fatal"


def classify_text(message: str) -> str:
    if _LIMIT.search(message or ""):
        return "limit"
    if _AUTH.search(message or ""):
        return "auth"
    if _TRANSIENT.search(message or ""):
        return "transient"
    return "fatal"


# ---------------------------------------------------------------- retry runner (R9)

def _backoffs() -> list[float]:
    raw = os.environ.get("ONEPROD_RETRY_BACKOFF", "2,8,30")
    return [float(x) for x in raw.split(",") if x.strip()]


async def run_with_retries(provider: Provider, account: dict, prompt: str,
                           model: str | None) -> AsyncIterator[Event]:
    """Retry transient failures only while nothing has been streamed yet. Once text has
    arrived the prompt is never re-sent (that would bill it twice); the failure is passed
    on marked interrupted so the caller can keep the partial reply."""
    delays = _backoffs()
    attempt = 0
    while True:
        got_text = False
        retry = False
        # aclosing: a stream we stop reading early (retry or failure) is closed now, so its
        # CLI process is cleaned up immediately instead of whenever garbage collection runs.
        async with aclosing(provider.stream(account, prompt, model)) as events:
            async for ev in events:
                if isinstance(ev, Text):
                    got_text = True
                    yield ev
                elif isinstance(ev, Fail):
                    if ev.kind == "transient" and not got_text and attempt < len(delays):
                        retry = True
                        break
                    ev.interrupted = got_text
                    yield ev
                    return
                else:
                    yield ev
        if not retry:
            return
        await asyncio.sleep(delays[attempt])
        attempt += 1
