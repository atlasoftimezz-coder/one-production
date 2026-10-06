"""Protected Resolve routes. Executing a plan is only possible here, i.e. by the owner in the UI."""
import asyncio

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import resolve_mcp
from .config import save_setting
from .resolve import MIN_VERSION, NOT_SUPPORTED, OPERATIONS, PlanError


class Toggle(BaseModel):
    enabled: bool | None = None       # One Production's own plan runner
    mcp: bool | None = None           # Resolve's own MCP server for the Claude agent (resolve_mcp.py)


class Execute(BaseModel):
    plan_hash: str = Field(min_length=64, max_length=64)
    confirm_saved: bool = False


def mount(app, resolve, project_check, web_dir):
    @app.get('/resolve.js')
    def script():
        return FileResponse(web_dir / 'resolve.js', media_type='text/javascript')

    @app.get('/api/resolve')
    async def status(probe: bool = False):
        out = {'enabled': resolve.enabled, 'min_version': MIN_VERSION, 'not_supported': NOT_SUPPORTED,
               'operations': {k: {'since': v['since'], 'fields': v['fields']} for k, v in OPERATIONS.items()},
               **resolve_mcp.status(resolve.settings)}
        if probe:
            out['status'] = await asyncio.to_thread(resolve.probe)
        return out

    @app.patch('/api/resolve')
    def toggle(body: Toggle):
        if body.enabled is not None:
            resolve.settings.resolve_enabled = body.enabled
            save_setting('resolve_enabled', body.enabled)
        if body.mcp is not None:
            resolve.settings.resolve_mcp = body.mcp
            save_setting('resolve_mcp', body.mcp)
        return {'enabled': resolve.enabled, **resolve_mcp.status(resolve.settings)}

    @app.get('/api/projects/{pid}/resolve/plans')
    def plans(pid: str):
        project_check(pid)
        return {'plans': resolve.list(pid)}

    @app.post('/api/projects/{pid}/resolve/plans')
    async def propose(pid: str, body: dict):
        project_check(pid)
        try:
            installed = await asyncio.to_thread(resolve.installed_version, pid)
            return await asyncio.to_thread(resolve.propose, pid, body, 'owner', installed)
        except PlanError as exc:
            raise HTTPException(400, str(exc))

    @app.post('/api/projects/{pid}/resolve/plans/{plan_id}/execute')
    async def execute(pid: str, plan_id: str, body: Execute):
        project_check(pid)
        try:
            return await asyncio.to_thread(resolve.execute, pid, plan_id, body.plan_hash, body.confirm_saved)
        except PlanError as exc:
            raise HTTPException(400, str(exc))

    @app.post('/api/projects/{pid}/resolve/plans/{plan_id}/discard')
    def discard(pid: str, plan_id: str):
        project_check(pid)
        try:
            return resolve.discard(pid, plan_id)
        except PlanError as exc:
            raise HTTPException(404, str(exc))
