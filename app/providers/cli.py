"""Running the official CLIs safely on Windows.

The prompt always goes in through stdin, never on the command line: npm installs the CLIs
as .cmd shims, and anything on a .cmd command line passes through cmd.exe, where characters
like & | > in user text would be interpreted. Our command-line arguments are constants.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import AsyncIterator

READ_LIMIT = 16 * 1024 * 1024     # one stream-json line can be large (tool results, signatures)


def resolve(name: str, override: str | None = None) -> str | None:
    """Find the CLI. An override (settings / env) wins; then PATH, which on Windows also
    matches name.cmd / name.exe via PATHEXT."""
    if override:
        return override if Path(override).exists() else shutil.which(override)
    return shutil.which(name)


async def run_lines(argv: list[str], stdin_text: str, env_extra: dict[str, str],
                    cwd: Path, timeout: float) -> AsyncIterator[tuple[str, str]]:
    """Start the process, feed stdin, yield ("out", line) for each stdout line, then
    ("exit", "<code>") and ("err", stderr). Kills the process on timeout."""
    env = {**os.environ, **env_extra}
    cwd.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env, cwd=str(cwd), limit=READ_LIMIT)
    proc.stdin.write(stdin_text.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()
    err_task = asyncio.create_task(proc.stderr.read())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            line = await asyncio.wait_for(proc.stdout.readline(), remaining)
            if not line:
                break
            yield "out", line.decode("utf-8", errors="replace").strip()
        code = await asyncio.wait_for(proc.wait(), max(deadline - loop.time(), 1))
        err = await err_task
        yield "exit", str(code)
        yield "err", err.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        yield "timeout", f"no response after {timeout:.0f}s"
    finally:
        # Runs on normal end, timeout, and when the caller stops reading early: the process
        # and both pipe readers are always finished before we return.
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if not err_task.done():
            err_task.cancel()
        try:
            await err_task
        except (asyncio.CancelledError, Exception):
            pass


async def run_capture(argv: list[str], env_extra: dict[str, str], cwd: Path,
                      timeout: float = 30) -> tuple[int, str, str]:
    env = {**os.environ, **env_extra}
    cwd.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env, cwd=str(cwd))
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", "timed out"
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
