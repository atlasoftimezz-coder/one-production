"""Built-in demo provider: no account, no network. Lets a brand-new user try the app and
keeps the streaming path testable without spending anyone's quota."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from .base import Event, Text, Usage


class Demo:
    name = "demo"

    async def status(self, account: dict) -> dict:
        return {"ok": True, "method": "built-in"}

    async def stream(self, account: dict, prompt: str, model: str | None,
                     effort: str | None = None) -> AsyncIterator[Event]:
        last = prompt.strip().rsplit("User:", 1)[-1].split("Assistant:", 1)[0].strip()
        text = (f"(Demo reply - connect a Claude, Codex or API-key account in Accounts for real "
                f"answers.) You said: {last}" if last else "(Demo reply) You sent an empty message.")
        for word in text.split(" "):
            yield Text(word + " ")
            await asyncio.sleep(0.03)
        yield Usage(input_tokens=len(prompt.split()), output_tokens=len(text.split()), model="demo")
