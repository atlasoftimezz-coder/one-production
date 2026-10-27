"""Source configuration, shared tools and read boundaries against simulated services."""
import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.agent import policy
from app.agent.studio import Studio
from app.agent_reach import handles_of
from app.source_mcp import CredentialStorage, OAuthLoopback
from app.security.guard import TOKEN_HEADER

TOKEN = 'source-test-token-0123456789abcdef'


class Model:
    def __init__(self, **value):
        self.__dict__.update(value)

    def model_dump(self, **kwargs):
        return self.__dict__


class FakeClient:
    def __init__(self):
        self.calls = []
        self.nodes = [{'type': 'n8n-nodes-base.discord', 'parameters': {'resource': 'message', 'operation': 'getAll'}}]

    async def list_tools(self, cursor=None):
        return SimpleNamespace(tools=[Model(name='read_reference', inputSchema={'type': 'object'})], next_cursor=None)

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == 'get_workflow_details':
            data = {'workflow': {'id': args['workflowId'], 'name': 'Final scripts', 'nodes': self.nodes}}
        elif name == 'execute_workflow':
            data = {'executionId': 'execution-123', 'status': 'started'}
        elif name == 'get_workflow_execution':
            data = {'execution': {'id': 'execution-123', 'status': 'success', 'workflowId': args['workflowId']},
                    'data': {'message_id': '123', 'content': 'A supplied final script', 'source_url': 'https://discord.com/channels/1/2/123'}}
        else:
            data = {'text': 'Reference result', 'arguments': args}
        return Model(structuredContent=data, content=[], isError=False)


@pytest.fixture
def sources_client(tmp_path, monkeypatch, memory_keyring):
    monkeypatch.setenv('ONEPROD_SETTINGS_FILE', str(tmp_path / 'settings.json'))
    fake = FakeClient()

    @asynccontextmanager
    async def factory(name, cfg, login):
        if login:
            login.update(state='waiting', url='https://login.example.test/authorize')
            await asyncio.sleep(.1)
            from mcp.shared.auth import OAuthToken
            await CredentialStorage(name, cfg['url']).set_tokens(OAuthToken(access_token='fake-source-token', token_type='Bearer', expires_in=3600))
        yield fake

    app = create_app(Settings(data_dir=tmp_path / 'data', storage={'drive': {'client_id': 'preserve-this-client'}}),
                     token=TOKEN, source_client_factory=factory)
    with TestClient(app, base_url='http://127.0.0.1', headers={TOKEN_HEADER: TOKEN}) as client:
        client.svc = app.state.sources
        client.fake = fake
        client.pid = client.post('/api/projects', json={'name': 'Source reference test'}).json()['id']
        client.scratch = tmp_path
        yield client


def connect(c, name='n8n', **updates):
    body = {'url': 'https://instance.example.test/mcp-server/http', 'auth': 'header', 'token': 'fake-mcp-access-token', **updates}
    r = c.put('/api/sources/connectors/' + name, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_connector_save_keeps_drive_and_secrets_out_of_settings(sources_client):
    c = sources_client
    data = connect(c)
    assert data['drive']['client_id'] == 'preserve-this-client'
    saved = (c.scratch / 'settings.json').read_text(encoding='utf-8')
    assert 'fake-mcp-access-token' not in saved
    assert 'fake-mcp-access-token' not in c.get('/api/sources').text
    assert data['connectors']['n8n']['has_token']
    from app import mcp_servers
    assert 'n8n' not in mcp_servers.for_agent(c.svc.settings)['servers']
    assert c.fake.calls == []
    # Blank preserves a token only on its original URL.
    connect(c, token='')
    r = c.put('/api/sources/connectors/n8n', json={'url': 'https://other.example.test/mcp', 'token': ''})
    assert r.status_code == 400
    assert c.svc.settings.mcp_servers['n8n']['url'] == 'https://instance.example.test/mcp-server/http'


@pytest.mark.parametrize('url', ['http://remote.example.test/mcp', 'https://user:secret@example.test/mcp', 'https://example.test/mcp#token'])
def test_unsafe_remote_urls_are_rejected(sources_client, url):
    assert sources_client.put('/api/sources/connectors/neon', json={'url': url, 'token': 'fake'}).status_code == 400


def test_browser_auth_stays_out_of_cli_settings_and_disconnect_removes_tokens(sources_client):
    c = sources_client
    connect(c, 'nexlev', url='https://nexlev.example.test/mcp', auth='oauth', token='')
    from app import mcp_servers
    assert 'nexlev' not in mcp_servers.for_agent(c.svc.settings)['servers']
    job = c.post('/api/sources/connectors/nexlev/login').json()
    import time
    for _ in range(40):
        status = c.get('/api/sources/login/' + job['id']).json()
        if status['state'] == 'connected':
            break
        time.sleep(.02)
    assert status['state'] == 'connected'
    assert 'listener' not in status and 'url_snapshot' not in status
    assert c.get('/api/sources').json()['connectors']['nexlev']['signed_in']
    connect(c, 'nexlev', url='https://nexlev.example.test/mcp', auth='oauth', token='', enabled=False)
    assert CredentialStorage('nexlev', 'https://nexlev.example.test/mcp').read(':tokens') is not None
    assert c.delete('/api/sources/connectors/nexlev').status_code == 200
    assert CredentialStorage('nexlev', 'https://nexlev.example.test/mcp').read(':tokens') is None


def test_tools_are_shared_and_arbitrary_calls_cannot_reuse_session_approval(sources_client):
    c = sources_client
    connect(c)
    studio = Studio(c.pid, c.svc.store, None, None, lambda job: None, sources=c.svc)
    tools = {t.name: t for t in studio.tools()}
    assert {'source_status', 'source_tools', 'source_call', 'drive_browse', 'drive_import', 'discord_read', 'x_read', 'x_digest'} <= tools.keys()
    result = asyncio.run(tools['source_tools'].handler({'server': 'n8n'}))
    assert json.loads(result)[0]['name'] == 'read_reference'
    for name in ('source_call', 'mcp__studio__source_call'):
        args = {'server': 'neon', 'tool': 'query', 'arguments': {'sql': 'DROP TABLE scripts'}}
        assert policy.decide(name, args, c.scratch)[0] == 'ask'
        assert policy.risky(name, args, c.scratch)


def test_discord_test_only_checks_metadata_and_read_is_manual(sources_client):
    c = sources_client
    connect(c)
    c.put('/api/sources/preferences/discord', json={'server': 'n8n', 'workflow_id': 'existing-reader', 'enabled': True})
    assert c.post('/api/sources/discord/test').status_code == 200
    assert [n for n, _ in c.fake.calls] == ['get_workflow_details']
    got = c.post('/api/sources/discord/read').json()
    operation = next(a for n, a in c.fake.calls if n == 'execute_workflow')
    assert operation['executionMode'] == 'manual' and operation['workflowId'] == 'existing-reader'
    assert operation['inputs']['type'] == 'chat'
    assert c.get('/api/sources/discord/executions/unknown').status_code == 400
    result = c.post(f'/api/projects/{c.pid}/sources/discord/import', json={'execution_id': got['executionId']})
    assert result.status_code == 200, result.text
    file = c.svc.store.file_path(c.pid, result.json()['file'])
    assert 'A supplied final script' in file.read_text(encoding='utf-8')


def test_discord_rejects_workflows_with_sending_nodes(sources_client):
    c = sources_client
    connect(c)
    c.put('/api/sources/preferences/discord', json={'workflow_id': 'reader', 'enabled': True})
    c.fake.nodes[0]['parameters']['operation'] = 'send'
    assert c.post('/api/sources/discord/read').status_code == 400
    assert not any(name == 'execute_workflow' for name, _ in c.fake.calls)


def test_x_handles_validation_and_batch_boundaries(sources_client, monkeypatch):
    c = sources_client
    assert handles_of('@OpenAI https://x.com/openai @n8n_io') == ['OpenAI', 'n8n_io']
    assert c.put('/api/sources/preferences/agent_reach', json={'handles': '@valid --delete', 'enabled': True}).status_code == 400
    c.put('/api/sources/preferences/agent_reach', json={'handles': '@one @two @three @four', 'enabled': True})
    calls = []

    async def read(action, handle, limit):
        calls.append(handle)
        return {'items': [{'text': 'Reference', 'url': f'https://x.com/{handle}/status/1'}]}

    monkeypatch.setattr(c.svc.reach, 'read', read)
    r = c.post(f'/api/projects/{c.pid}/sources/x/import', json={'count': 2, 'offset': 0}).json()
    assert calls == ['one', 'two'] and r['next_offset'] == 2
    assert c.svc.store.file_path(c.pid, r['file']).exists()
    async def blocked(*args):
        raise ValueError('PLATFORM_BLOCKED: owner verification required')
    monkeypatch.setattr(c.svc.reach, 'read', blocked)
    assert c.post(f'/api/projects/{c.pid}/sources/x/import', json={}).status_code == 400


def test_oauth_loopback_rejects_wrong_state():
    listener = OAuthLoopback()
    try:
        assert httpx.get(listener.url, params={'state': 'wrong', 'code': 'x'}).status_code == 400
        assert listener.result is None
        assert httpx.get(listener.url, params={'state': listener.state, 'code': 'ok', 'iss': 'https://issuer.test'}).status_code == 200
        assert listener.result['code'] == 'ok' and listener.result['iss'] == 'https://issuer.test'
    finally:
        listener.close()


def test_real_mcp_sdk_lists_and_calls_a_local_stdio_server(tmp_path):
    import sys
    from pathlib import Path
    from app.source_mcp import SourceMcp
    script = Path(__file__).parent / 'fakes' / 'source_mcp_stdio.py'
    settings = Settings(data_dir=tmp_path, mcp_servers={'local': {'type': 'stdio', 'command': sys.executable, 'args': [str(script)]}})
    svc = SourceMcp(settings)
    async def exercise():
        names = [t['name'] for t in await svc.tools('local')]
        result = await svc.call('local', 'read_reference', {'query': 'a supplied reference'})
        assert names == ['read_reference']
        assert result['structuredContent']['query'] == 'a supplied reference'
    asyncio.run(exercise())
