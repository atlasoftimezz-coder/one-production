"""Authenticated local CLI transport and a session-scoped studio MCP bridge."""
from __future__ import annotations

import asyncio
import hmac
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .accounts import AccountError
from .agent import policy
from .agent.studio import call
from .terminal import BRIDGE_HEADER, TerminalError
from .terminal_providers import PROVIDERS, command, engine_for
from .security.guard import LOCAL_HOSTNAMES
from .storage import NotFound


class StartTerminal(BaseModel):
    account_id: str | None = None
    provider: str | None = None
    cwd: str = Field(default="", max_length=4096)
    config_dir: str = Field(default="", max_length=4096)
    rows: int = Field(default=32, ge=5, le=250)
    cols: int = Field(default=100, ge=20, le=500)
    fresh: bool = False
    studio: bool = True


class ToolCall(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)


def mount(app, terminals, accounts, store, profile, token, studio_for, *, busy, instructions, web_dir):
    terminals.external_busy = busy
    def project(pid):
        try:
            store.project(pid)
        except NotFound:
            raise HTTPException(404, "Choose an existing project first.") from None

    @app.get('/claude-code.js')
    @app.get('/terminal.js')
    def script():
        return FileResponse(web_dir / 'terminal.js', media_type='text/javascript')

    @app.get('/claude-code.css')
    @app.get('/terminal.css')
    def css():
        return FileResponse(web_dir / 'claude-code.css', media_type='text/css')

    @app.get('/vendor/xterm/{name}')
    def vendor(name: str):
        if name not in {'xterm.js', 'addon-fit.js', 'xterm.css'}:
            raise HTTPException(404)
        return FileResponse(web_dir / 'vendor' / 'xterm' / name,
                            media_type='text/css' if name.endswith('.css') else 'text/javascript')

    @app.get('/api/projects/{pid}/claude-code')
    @app.get('/api/projects/{pid}/terminal')
    async def state(pid: str, request: Request):
        project(pid)
        saved = terminals.preferences(pid)
        all_accounts = accounts.list()
        legacy = request.url.path.endswith('/claude-code')
        items = [a for a in all_accounts if engine_for(a) and (not legacy or engine_for(a) == 'claude')]
        session = terminals.live.get(pid)
        return {'session': session.public() if session else None,
                'preferences': {k: saved.get(k) for k in ('cwd', 'config_dir', 'account_id', 'studio', 'provider', 'providers')},
                'providers': [{'id': p.id, 'label': p.label, 'needs_account': p.needs_account,
                               'help': p.help, 'local_config_dir': p.local_config(),
                               'installed': bool(command(p.id, terminals.commands.get(p.id)))} for p in PROVIDERS.values()],
                'default_cwd': str(store.files_dir(pid)),
                'data_dir': str(store.root.parent),
                'other_account_count': len(all_accounts) - len(items),
                'local_config_dir': os.environ.get('CLAUDE_CONFIG_DIR') or str(Path.home() / '.claude'),
                'accounts': [{'id': a['id'], 'label': a.get('label') or a.get('email') or a['id'],
                              'provider': a['provider'], 'engine': engine_for(a), 'config_dir': a.get('profile_dir') or
                              str(store.root.parent / 'profiles' / f"agent-{a['id']}"),
                              'enabled': a['enabled'],
                              'can_share_setup': bool(a.get('token_hint') or a['provider'] == 'anthropic_api')}
                             for a in items]}

    @app.post('/api/projects/{pid}/claude-code')
    @app.post('/api/projects/{pid}/terminal')
    async def start(pid: str, body: StartTerminal, request: Request):
        project(pid)
        if busy(pid):
            raise HTTPException(409, 'The studio agent is working here. Stop it before starting a CLI.')
        try:
            provider = body.provider or ('claude' if request.url.path.endswith('/claude-code') else 'shell')
            adapter = PROVIDERS.get(provider)
            if not adapter:
                raise TerminalError('Choose a supported CLI.')
            account = accounts.get(body.account_id) if body.account_id and adapter.needs_account else None
            session = await terminals.start(pid, account, provider=provider,
                                            **body.model_dump(exclude={'account_id', 'provider'}),
                                            base_url=str(request.base_url), instructions=instructions(pid))
        except (TerminalError, AccountError) as exc:
            raise HTTPException(400, str(exc)) from None
        return session.public()

    @app.delete('/api/projects/{pid}/claude-code/{sid}')
    @app.delete('/api/projects/{pid}/terminal/{sid}')
    async def stop(pid: str, sid: str):
        project(pid)
        session = terminals.live.get(pid)
        if not session or session.id != sid:
            raise HTTPException(404, 'That CLI session is no longer open.')
        await session.close()
        return {'ok': True}

    def bridge(sid, request):
        session = next((s for s in terminals.live.values() if s.id == sid and not s.ended), None)
        sent = request.headers.get(BRIDGE_HEADER, '')
        if (profile.locked or not session or not sent or
                not hmac.compare_digest(sent.encode(), session.bridge_key.encode())):
            raise HTTPException(403, 'The studio bridge is unavailable.')
        project(session.pid)
        return session

    @app.get('/claude-code-bridge/{sid}/tools')
    async def tools(sid: str, request: Request):
        session = bridge(sid, request)
        return [{'name': t.name, 'description': t.description, 'inputSchema': t.schema}
                for t in studio_for(session.pid).tools()]

    @app.post('/claude-code-bridge/{sid}/permission')
    async def permission(sid: str, body: ToolCall, request: Request):
        session = bridge(sid, request)
        required = body.name in policy.STUDIO_GATED | policy.SOURCE_REQUESTS
        title, detail = policy.describe('mcp__studio__' + body.name, body.arguments, store.files_dir(session.pid))
        return {'required': required, 'mode': 'elicitation' if session.provider == 'codex' else 'hook',
                'message': f'One Production: {title}\n{detail}\nAllow this operation once?'}

    @app.post('/claude-code-bridge/{sid}/approval')
    async def approval(sid: str, body: ToolCall, request: Request):
        session = bridge(sid, request)
        if session.provider != 'codex':
            raise HTTPException(403, 'Use the native permission hook for this CLI.')
        session.studio_request(body.name, body.arguments, approve=True)
        return {'ok': True}

    @app.post('/claude-code-bridge/{sid}/call')
    async def tool_call(sid: str, body: ToolCall, request: Request):
        session = bridge(sid, request)
        tools = {t.name: t for t in studio_for(session.pid).tools()}
        if body.name not in tools:
            raise HTTPException(404, 'Unknown studio tool.')
        if (body.name in policy.STUDIO_GATED | policy.SOURCE_REQUESTS and
                not session.studio_request(body.name, body.arguments)):
            raise HTTPException(403, 'This studio operation requires a fresh native permission prompt. '
                                     'Allow the CLI permission request and try again.')
        text, error = await call(tools[body.name], body.arguments)
        return {'text': text, 'error': error}

    @app.post('/claude-code-bridge/{sid}/policy')
    async def tool_policy(sid: str, request: Request, body: dict):
        session = bridge(sid, request)
        if session.provider != 'claude':
            raise HTTPException(403, 'This CLI uses MCP approval requests instead of Claude hooks.')
        name = str(body.get('tool_name', '')).removeprefix('mcp__oneproduction__')
        # These cards must appear on every request, even if a native MCP allow rule
        # exists. The native CLI displays and collects the permission decision.
        if name in policy.STUDIO_GATED | policy.SOURCE_REQUESTS:
            session.studio_request(name, body.get('tool_input') or {}, approve=True)
            title, detail = policy.describe('mcp__studio__' + name, body.get('tool_input') or {},
                                            store.files_dir(session.pid))
            return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'ask',
                    'permissionDecisionReason': f'One Production: {title}. {detail}'}}
        return {}

    @app.websocket('/api/projects/{pid}/claude-code/{sid}/socket')
    @app.websocket('/api/projects/{pid}/terminal/{sid}/socket')
    async def socket(ws: WebSocket, pid: str, sid: str):
        # HTTP middleware never runs for WebSockets. Authenticate here explicitly,
        # with the token in the first frame (never in a URL, cookie or log).
        host, origin = ws.headers.get('host', ''), ws.headers.get('origin', '')
        try:
            origin_url = urlsplit(origin)
            host_url = urlsplit('//' + host)
        except ValueError:
            await ws.close(code=4403)
            return
        if (host_url.hostname not in LOCAL_HOSTNAMES or origin_url.scheme not in ('http', 'https') or
                origin_url.netloc.lower() != host.lower() or profile.locked):
            await ws.close(code=4403)
            return
        await ws.accept()
        try:
            auth = await asyncio.wait_for(ws.receive_json(), 5)
            sent = auth.get('token', '')
            if not isinstance(sent, str) or not hmac.compare_digest(sent.encode(), token.encode()):
                await ws.close(code=4401)
                return
        except (asyncio.TimeoutError, ValueError, AttributeError, WebSocketDisconnect):
            await ws.close(code=4401)
            return
        session = terminals.live.get(pid)
        if not session or session.id != sid:
            await ws.close(code=4404)
            return
        q = asyncio.Queue(maxsize=512)
        mark, replay = session.seq, list(session.output)
        session.listeners.append(q)

        async def output():
            await ws.send_json({'t': 'ready', **session.public()})
            for item in replay:
                if profile.locked:
                    return
                await ws.send_json(item)
            if session.ended:
                await ws.send_json({'t': 'exit', 'code': session.exit_code})
                return
            while not profile.locked:
                try:
                    event = await asyncio.wait_for(q.get(), 3)
                except asyncio.TimeoutError:
                    await ws.send_json({'t': 'ping'})
                    continue
                if profile.locked:
                    return
                if event.get('seq', mark + 1) <= mark:
                    continue
                await ws.send_json(event)
                if event['t'] in ('exit', 'reconnect'):
                    return

        async def input_():
            while not profile.locked:
                item = await ws.receive_json()
                if profile.locked:
                    return
                if item.get('t') == 'input':
                    data = item.get('data')
                    if not isinstance(data, str) or len(data) > 32768:
                        return
                    await session.write(data)
                elif item.get('t') == 'resize':
                    rows, cols = item.get('rows'), item.get('cols')
                    if (type(rows) is not int or type(cols) is not int or
                            not 5 <= rows <= 250 or not 20 <= cols <= 500):
                        return
                    await session.resize(rows, cols)

        tasks = [asyncio.create_task(output()), asyncio.create_task(input_())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if q in session.listeners:
                session.listeners.remove(q)
            try:
                await ws.close()
            except (RuntimeError, WebSocketDisconnect):
                pass
