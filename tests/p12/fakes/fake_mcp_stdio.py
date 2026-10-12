"""A stand-in stdio MCP server: a banner line, then JSON-RPC over newline-delimited JSON."""
import json
import sys

print("fake mcp server ready", flush=True)          # some servers print a banner first
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize":
        out = {"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-03-26", "capabilities": {}}}
    elif msg.get("method") == "tools/list":
        out = {"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{"name": "read_file"}, {"name": "list_dir"}]}}
    else:
        continue
    print(json.dumps(out), flush=True)
