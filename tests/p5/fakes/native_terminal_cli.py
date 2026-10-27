"""TTY-only test program. Exercises native transport and the real MCP adapter, no model calls."""
import asyncio
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
program=os.environ.get('ONEPROD_CLI_PROVIDER','claude')
if '--version' in sys.argv:
    assistant=sys.argv[sys.argv.index('--assistant')+1] if '--assistant' in sys.argv else program
    print(assistant+' test CLI version',flush=True)
    raise SystemExit
if 'login' in sys.argv:
    print('Logged in using ChatGPT',flush=True)
    raise SystemExit
if 'auth' in sys.argv:
    print(json.dumps({'loggedIn':True,'authMethod':'oauth_token','email':'terminal-test@example.test'}))
    raise SystemExit


async def mcp_probe():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    if program=='codex':
        config=tomllib.loads(sys.argv[sys.argv.index('-c')+1])['mcp_servers']['oneproduction']
    else:
        config=json.loads(sys.argv[sys.argv.index('--mcp-config')+1])['mcpServers']['oneproduction']
        settings=json.loads(sys.argv[sys.argv.index('--settings')+1])
        hook=settings['hooks']['PreToolUse'][0]['hooks'][0]['command']
        check=subprocess.run(hook,shell=True,input=json.dumps({'tool_name':'mcp__oneproduction__source_call','tool_input':{}}),
                             capture_output=True,text=True,encoding='utf-8',timeout=20)
        assert json.loads(check.stdout)['hookSpecificOutput']['permissionDecision']=='ask', check.stderr
        print('Native permission hook: ask',flush=True)
    approve=False
    async def elicitation(context,params):
        from mcp.types import ElicitResult
        print('Native MCP approval: '+('accept' if approve else 'decline'),flush=True)
        return ElicitResult(action='accept' if approve else 'decline',content={'approve':True} if approve else None)
    params=StdioServerParameters(command=config['command'],args=config['args'],
                                env={k:os.environ[k] for k in ('ONEPROD_BRIDGE_URL','ONEPROD_BRIDGE_KEY')})
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write,elicitation_callback=elicitation) as session:
            await session.initialize()
            tools=await session.list_tools()
            assert 'source_call' in {t.name for t in tools.tools}
            result=await session.call_tool('script_stats',{'file':'ep1.md'})
            print('MCP connected: oneproduction; script_stats = '+result.content[0].text,flush=True)
            if program=='codex':
                result=await session.call_tool('source_tools',{'source':'not-configured'})
                assert result.is_error and 'not approve' in result.content[0].text
                approve=True
                result=await session.call_tool('source_tools',{'source':'not-configured'})
                assert 'not approve' not in result.content[0].text
                print('Native MCP approvals verified',flush=True)


print('\x1b[38;2;255;107;74m'+program+' · native terminal test\x1b[0m',flush=True)
print('Working folder: '+str(Path.cwd()),flush=True)
instructions='AGENTS.md' if program=='codex' else 'CLAUDE.md'
print(instructions+': '+Path(instructions).read_text(encoding='utf-8'),flush=True)
while True:
    try:
        text=input('❯ ')
    except (EOFError,KeyboardInterrupt):
        break
    if text=='/exit':
        break
    if text=='/help':
        print('Native commands: /help /mcp /write /exit',flush=True)
    elif text=='/mcp':
        asyncio.run(mcp_probe())
    elif text=='/write':
        Path('native-result.md').write_text('Native CLI wrote this file.\n',encoding='utf-8')
        print('Wrote native-result.md',flush=True)
    else:
        print('Received: '+text,flush=True)
