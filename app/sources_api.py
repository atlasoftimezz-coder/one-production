"""Source setup and deliberate reads. Test buttons do not execute workflows."""
from fastapi import HTTPException
from .source_mcp import error_text
from .storage import NotFound


def mount(app, sources, store):
    def sync(fn, *args):
        try:
            return fn(*args)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    async def run(fn, *args):
        try:
            return await fn(*args)
        except Exception as exc:
            raise HTTPException(400, error_text(exc)) from None

    def project(pid):
        try:
            store.project(pid)
        except NotFound:
            raise HTTPException(404, "Choose an existing project first.") from None

    @app.get('/api/sources')
    def state():
        return sources.state()

    @app.put('/api/sources/connectors/{name}')
    def save(name: str, body: dict):
        return sync(sources.save_connector, name, body)

    @app.delete('/api/sources/connectors/{name}')
    def remove(name: str):
        return sync(sources.remove_connector, name)

    @app.put('/api/sources/preferences/{name}')
    def preferences(name: str, body: dict):
        return sync(sources.save_preferences, name, body)

    @app.post('/api/sources/connectors/{name}/test')
    async def test(name: str):
        return {'ok': True, 'tools': [t['name'] for t in await run(sources.mcp.tools, name)]}

    @app.post('/api/sources/connectors/{name}/login')
    async def login(name: str):
        return sync(sources.mcp.start_login, name)

    @app.get('/api/sources/login/{job_id}')
    def login_status(job_id: str):
        job = sources.mcp.jobs.get(job_id)
        if not job:
            raise HTTPException(404, 'This sign-in is over. Start again.')
        return sources.mcp.public_job(job)

    @app.delete('/api/sources/login/{job_id}')
    async def cancel_login(job_id: str):
        job = sources.mcp.jobs.get(job_id)
        if job:
            sources.mcp.cancel(job['source'])
        return {'ok': True}

    @app.post('/api/sources/discord/test')
    async def test_discord():
        return await run(sources.test_discord)

    @app.post('/api/sources/discord/read')
    async def discord_read():
        return await run(sources.read_discord)

    @app.get('/api/sources/discord/executions/{execution_id}')
    async def discord_result(execution_id: str):
        return await run(sources.discord_result, execution_id)

    @app.post('/api/projects/{pid}/sources/discord/import')
    async def discord_import(pid: str, body: dict):
        project(pid)
        result = await run(sources.discord_result, str(body.get('execution_id', '')))
        if (result.get('execution') or {}).get('status') != 'success':
            raise HTTPException(400, 'Wait for the Discord read to succeed before importing.')
        return sync(sources.save_result, pid, 'discord', result)

    @app.post('/api/sources/agent-reach/check')
    async def reach_check():
        return await run(sources.reach.run, 'doctor')

    @app.post('/api/sources/agent-reach/actions/{action}')
    async def reach_action(action: str):
        return sync(sources.reach.start, action)

    @app.get('/api/sources/agent-reach/jobs/{job_id}')
    def reach_job(job_id: str):
        if job_id not in sources.reach.jobs:
            raise HTTPException(404, 'Unknown Agent Reach setup job.')
        return sources.reach.jobs[job_id]

    @app.post('/api/projects/{pid}/sources/x/import')
    async def import_x(pid: str, body: dict):
        project(pid)
        result = await run(sources.reach.digest, body.get('offset', 0), body.get('count', 3), body.get('limit', 5))
        return {**sync(sources.save_result, pid, 'x', result), **result}
