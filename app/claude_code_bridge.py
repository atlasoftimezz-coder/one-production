"""Private stdio MCP adapter for native CLI sessions; also Claude's studio-only hook.

Run as a file, so it works with any selected working folder. No account credentials,
app session token, shell, or public listener is used by this adapter.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx


async def request(path, data=None):
    async with httpx.AsyncClient(timeout=300, trust_env=False) as client:
        response = await client.request("GET" if data is None else "POST",
            os.environ["ONEPROD_BRIDGE_URL"] + path, json=data,
            headers={"X-OneProd-Bridge": os.environ["ONEPROD_BRIDGE_KEY"]})
        response.raise_for_status()
        return response.json()


async def serve():
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

    async def tools(context, params):
        return ListToolsResult(tools=[Tool(**tool) for tool in await request("/tools")])

    async def call(context, params):
        try:
            payload = {"name": params.name, "arguments": params.arguments or {}}
            permission = await request('/permission', payload)
            if permission['required'] and permission['mode'] == 'elicitation':
                answer = await context.session.elicit_form(permission['message'],
                    {'type': 'object', 'properties': {'approve': {'type': 'boolean',
                        'title': 'Allow once', 'default': False}}, 'required': ['approve']},
                    related_request_id=context.request_id)
                if answer.action != 'accept' or (answer.content or {}).get('approve') is not True:
                    return CallToolResult(content=[TextContent(type='text', text='The user did not approve this operation.')], isError=True)
                await request('/approval', payload)
            result = await request("/call", payload)
            return CallToolResult(content=[TextContent(type="text", text=result["text"])], isError=result["error"])
        except Exception:
            return CallToolResult(content=[TextContent(type="text", text="One Production could not run this tool. "
                                      "Check that the app is open, unlocked, and the CLI session is connected.")], isError=True)

    server = Server("oneproduction", on_list_tools=tools, on_call_tool=call,
                    instructions='One Production studio tools operate in the project files folder: ' +
                    os.environ.get('ONEPROD_PROJECT_FILES', '(inspect project tool paths)') +
                    '. This can differ from the native CLI working folder. Discover connected sources with source_status and source_tools. '
                    'Studio GPU/paid operations and source calls need fresh user approval. Follow job results and preserve approved outputs.')
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


async def policy():
    try:
        data = json.load(sys.stdin)
        result = await asyncio.wait_for(request("/policy", data), 15)
    except Exception:
        result = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                  "permissionDecisionReason": "One Production is unavailable or locked. Reconnect before using studio tools."}}
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(policy() if "--policy" in sys.argv else serve())
