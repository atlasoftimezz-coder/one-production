"""Chat replies. In P1 the reply is a local stub so the streaming path can be built and
tested without any account; P2 swaps in real providers behind the same function."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator


async def stub_reply(message: str) -> AsyncIterator[str]:
    text = (f"(P1 test reply - no AI account connected yet.) You said: {message.strip()}"
            if message.strip() else "(P1 test reply) You sent an empty message.")
    for word in text.split(" "):
        yield word + " "
        await asyncio.sleep(0.03)
