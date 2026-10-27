"""A complete local protocol peer for the source client's SDK boundary check."""
import json
import sys

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get('method')
    if method == 'initialize':
        result = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'source-test', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'read_reference', 'description': 'Read a test reference', 'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'string'}}}}]}
    elif method == 'tools/call':
        result = {'content': [{'type': 'text', 'text': 'A test reference'}], 'structuredContent': msg.get('params', {}).get('arguments', {})}
    else:
        continue
    print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result}), flush=True)
