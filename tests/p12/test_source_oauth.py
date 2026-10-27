"""Exercise the real MCP OAuth client against a loopback-only authorization server."""
import asyncio
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlparse

import httpx

from app.config import Settings
from app.source_mcp import CredentialStorage, SourceMcp


def test_sdk_oauth_registration_pkce_storage_and_later_tool_access(tmp_path, memory_keyring):
    registered, grants = [], []

    class Handler(BaseHTTPRequestHandler):
        def reply(self, value, status=200, headers=None):
            raw = json.dumps(value).encode() if value is not None else b''
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            for key, val in (headers or {}).items():
                self.send_header(key, val)
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if 'oauth-protected-resource' in self.path:
                self.reply({'resource': base + '/mcp', 'authorization_servers': [base], 'scopes_supported': ['read']})
            elif 'oauth-authorization-server' in self.path or 'openid-configuration' in self.path:
                self.reply({'issuer': base, 'authorization_endpoint': base + '/authorize', 'token_endpoint': base + '/token',
                    'registration_endpoint': base + '/register', 'response_types_supported': ['code'],
                    'grant_types_supported': ['authorization_code', 'refresh_token'], 'code_challenge_methods_supported': ['S256'],
                    'token_endpoint_auth_methods_supported': ['none']})
            else:
                self.reply(None, 405)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            if self.path == '/register':
                data = json.loads(raw)
                registered.append(data)
                self.reply({**data, 'client_id': 'loopback-client'}, 201)
                return
            if self.path == '/token':
                grants.append(parse_qs(raw.decode()))
                self.reply({'access_token': 'loopback-access-token', 'refresh_token': 'loopback-refresh-token',
                    'token_type': 'Bearer', 'expires_in': 3600, 'scope': 'read'})
                return
            if self.headers.get('Authorization') != 'Bearer loopback-access-token':
                self.reply({'error': 'unauthorized'}, 401, {'WWW-Authenticate': f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'})
                return
            msg = json.loads(raw)
            if msg.get('method') == 'notifications/initialized':
                self.reply(None, 202)
                return
            if msg.get('method') == 'initialize':
                result = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'oauth-test', 'version': '1'}}
            else:
                result = {'tools': [{'name': 'read_reference', 'inputSchema': {'type': 'object'}}]}
            self.reply({'jsonrpc': '2.0', 'id': msg['id'], 'result': result})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    base = f'http://127.0.0.1:{server.server_address[1]}'
    threading.Thread(target=server.serve_forever, daemon=True).start()
    svc = SourceMcp(Settings(data_dir=tmp_path, mcp_servers={'oauth': {'type': 'http', 'url': base + '/mcp', 'auth': 'oauth'}}))

    async def exercise():
        job = svc.start_login('oauth')
        for _ in range(100):
            current = svc.jobs[job['id']]
            if current['state'] in ('waiting', 'failed'):
                break
            await asyncio.sleep(.05)
        assert current['state'] == 'waiting', current.get('error')
        query = parse_qs(urlparse(current['url']).query)
        callback = await asyncio.to_thread(httpx.get, query['redirect_uri'][0], params={'state': query['state'][0], 'code': 'test-code', 'iss': base})
        assert callback.status_code == 200
        await asyncio.wait_for(svc.tasks['oauth'], 10)
        assert current['state'] == 'connected', current.get('error')
        token = await CredentialStorage('oauth', base + '/mcp').get_tokens()
        assert token.access_token == 'loopback-access-token'
        verifier = grants[0]['code_verifier'][0]
        assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=') == query['code_challenge'][0]
        assert registered[0]['redirect_uris'] == [query['redirect_uri'][0]]
        assert [t['name'] for t in await svc.tools('oauth')] == ['read_reference']
        assert len(registered) == 1
        generation = svc.generations.get('oauth', 0)
        stale = CredentialStorage('oauth', base + '/mcp', guard=lambda: svc.generations.get('oauth', 0) == generation, lock=svc.auth_lock)
        svc.forget('oauth', svc.settings.mcp_servers['oauth'])
        try:
            stale.write(':tokens', {'access_token': 'late-grant'})
        except ValueError:
            pass
        else:
            raise AssertionError('A cancelled sign-in must not restore credentials.')
        assert stale.read(':tokens') is None
        await svc.close()

    try:
        asyncio.run(exercise())
    finally:
        server.shutdown()
        server.server_close()
