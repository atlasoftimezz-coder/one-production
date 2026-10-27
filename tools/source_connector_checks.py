"""Browser interaction checks for source cards; all accounts and results are simulated."""
import os
from pathlib import Path


def check_source_connectors(js, wait_for, check, shot, send, out):
    def preview(name):
        shot(name)
        if directory := os.environ.get('ONEPROD_UI_PREVIEW_DIR'):
            target = Path(directory)
            target.mkdir(parents=True, exist_ok=True)
            (target / f'{name}.png').write_bytes((out / f'{name}.png').read_bytes())

    js(r"""{
      const mock = window.sourceUiMock = {fetch: window.fetch, open: window.open, calls: [], failTest: false, polls: 0,
        connectors: Object.fromEntries(['n8n','neon','nexlev'].map(name => [name, {title:name,url:name === 'n8n' ? '' : `https://${name}.example.test/mcp`,auth:name === 'nexlev' ? 'oauth' : 'header',configured:false,enabled:false,has_token:false,signed_in:false,help:'Connect your account.'}])),
        discord:{server:'n8n',workflow_id:'existing-discord-reader',enabled:false},
        reach:{installed:true,enabled:false,command:'',handles:[],experimental:true,jobs:[]}};
      const reply = (body, status=200) => new Response(JSON.stringify(body), {status, headers:{'Content-Type':'application/json'}});
      window.open = () => null;
      window.fetch = async (input, options={}) => {
        const path = new URL(typeof input === 'string' ? input : input.url, location.origin).pathname;
        if (!path.startsWith('/api/sources') && !/^\/api\/projects\/[^/]+\/sources\//.test(path) && path !== '/api/mcp-servers') return mock.fetch.call(window,input,options);
        const method = options.method || 'GET', body = options.body ? JSON.parse(options.body) : null;
        mock.calls.push({path,method,body});
        if (path === '/api/sources') return reply({connectors:mock.connectors,discord:mock.discord,agent_reach:mock.reach,servers:['n8n'],agents:['Claude','Codex']});
        if (path === '/api/mcp-servers') return reply({servers:Object.fromEntries(Object.entries(mock.connectors).filter(([_,c])=>c.configured).map(([n,c])=>[n,{type:'http',url:c.url,auth:c.auth}]))});
        const connector = path.match(/^\/api\/sources\/connectors\/(n8n|neon|nexlev)(?:\/(test|login))?$/);
        if (connector) {
          const [,name,action] = connector, cfg = mock.connectors[name];
          if (method === 'PUT') { Object.assign(cfg,body,{configured:true,has_token:!!body.token || cfg.has_token}); return reply({}); }
          if (method === 'DELETE') { Object.assign(cfg,{configured:false,signed_in:false,enabled:false,has_token:false}); return reply({}); }
          if (action === 'test') return mock.failTest ? reply({detail:'Access token expired. Connect again.'},400) : reply({ok:true,tools:['read_reference','search_references']});
          if (action === 'login') { mock.polls=0; return reply({id:'source-login',state:'starting'}); }
        }
        if (path === '/api/sources/login/source-login') {
          if (method === 'DELETE') return reply({ok:true});
          if (++mock.polls < 2) return reply({id:'source-login',state:'waiting',url:'https://login.example.test/authorize'});
          mock.connectors.nexlev.signed_in=true;
          return reply({id:'source-login',state:'connected',tools:['read_reference']});
        }
        if (path === '/api/sources/preferences/discord') {Object.assign(mock.discord,body);return reply({});}
        if (path === '/api/sources/preferences/agent_reach') {Object.assign(mock.reach,body,{handles:body.handles.split(/\s+/).filter(Boolean)});return reply({});}
        if (path === '/api/sources/discord/test') return reply({workflow:{name:'Final Discord Scripts'},message:'Workflow found. No messages were read.'});
        if (path === '/api/sources/discord/read') return reply({executionId:'discord-read',status:'started'});
        if (path === '/api/sources/discord/executions/discord-read') return reply({execution:{status:'success'},messages:[{timestamp:'2026-10-28',author:'Creator',content:'A final script supplied by the owner.',source_url:'https://discord.com/channels/1/2/3',attachments:[]}]});
        if (path.endsWith('/sources/discord/import')) return reply({file:'sources/discord/scripts.json'});
        if (path === '/api/sources/agent-reach/check') return reply({ok:true,result:{channels:[{name:'twitter',status:'ready'}]}});
        if (path.endsWith('/sources/x/import')) return reply({file:'sources/x/posts.json',accounts:[{handle:'example'}],next_offset:null});
        return reply({detail:'Unexpected simulated source request'},400);
      };
    }""")
    try:
        js("document.querySelector('#mcpbtn').click()")
        check('Source cards load without claiming access', wait_for("document.querySelector('#source_n8n [data-source-state]').textContent === 'Not connected'"))
        check('Nexlev browser sign-in hides the API token field', js("getComputedStyle(document.querySelector('#source_nexlev [data-source-token-label]')).display === 'none'"))
        js("document.querySelector('#source_n8n details').open=true; document.querySelector('#source_n8n [data-source-url]').value='https://n8n.example.test/mcp-server/http'; document.querySelector('#source_n8n [data-source-token]').value='simulated-ui-token'; document.querySelector('#source_n8n form').requestSubmit()")
        check('n8n form saves and clears the token', wait_for("sourceUiMock.connectors.n8n.configured && document.querySelector('#source_n8n [data-source-token]').value === ''"))
        js("document.querySelector('#source_n8n [data-source-test]').click()")
        check('n8n connection test lists discovered tools', wait_for("document.querySelector('#source_n8n [data-source-result]').textContent.includes('read_reference')"))
        js("sourceUiMock.failTest=true; document.querySelector('#source_n8n [data-source-test]').click()")
        check('Source access errors are visible', wait_for("document.querySelector('#source_n8n [data-source-result]').classList.contains('error')"))
        js("sourceUiMock.failTest=false; document.querySelector('#source_n8n [data-source-test]').click()")
        check('Source tests can recover after an error', wait_for("document.querySelector('#source_n8n [data-source-state]').textContent === 'Verified'"))
        js("document.querySelector('#source_nexlev [data-source-login]').click()")
        check('OAuth offers a popup fallback link', wait_for("!document.querySelector('#source_nexlev [data-source-signin]').hidden"))
        check('Nexlev displays completed browser sign-in', wait_for("document.querySelector('#source_nexlev [data-source-state]').textContent === 'Signed in'"))
        js("document.querySelector('#source_neon details').open=true; document.querySelector('#source_neon [data-source-token]').value='simulated-neon-key'; document.querySelector('#source_neon form').requestSubmit()")
        check('Neon has a separate saved connection', wait_for("sourceUiMock.connectors.neon.configured"))
        js("document.querySelector('#source_neon [data-source-test]').click()")
        check('Neon verifies independently', wait_for("document.querySelector('#source_neon [data-source-state]').textContent === 'Verified'"))
        js("document.querySelector('#source_n8n details').open=false; document.querySelector('#source_neon details').open=false; document.querySelector('#source_connections').scrollIntoView()")
        preview('sources-connections')

        js("document.querySelector('#source_discord details').open=true; document.querySelector('#discord_enabled').checked=true; document.querySelector('#discord_form').requestSubmit()")
        check('Discord workflow binding saves', wait_for("!document.querySelector('#discord_test').disabled"))
        js("document.querySelector('#discord_test').click()")
        check('Discord Test does not execute the workflow', wait_for("document.querySelector('#discord_result').textContent.includes('No messages were read')") and js("!sourceUiMock.calls.some(c => c.path === '/api/sources/discord/read')"))
        js("document.querySelector('#discord_read').click()")
        check('Discord read displays script text and source link', wait_for("document.querySelector('#discord_result').textContent.includes('A final script supplied by the owner.') && document.querySelector('#discord_result').textContent.includes('https://discord.com/channels/1/2/3')"))
        js("document.querySelector('#discord_import').click()")
        check('Discord result can be imported into the project', wait_for("document.querySelector('#discord_result').textContent.includes('Imported sources/discord/scripts.json')"))
        js("document.querySelector('#source_discord').scrollIntoView()")
        preview('sources-discord-reader')

        js("document.querySelector('#reach_handles').value='@example @another'; document.querySelector('#reach_enabled').checked=true; document.querySelector('#reach_form').requestSubmit()")
        check('Agent Reach saves the followed account list', wait_for("sourceUiMock.reach.handles.length === 2 && !document.querySelector('#reach_collect').disabled"))
        js("document.querySelector('#reach_check').click()")
        check('Agent Reach shows local diagnostics', wait_for("document.querySelector('#reach_result').textContent.includes('twitter')"))
        js("document.querySelector('#reach_collect').click()")
        check('X posts import reports saved source file', wait_for("document.querySelector('#reach_result').textContent.includes('sources/x/posts.json')"))
        check('X collection uses a bounded batch', js("sourceUiMock.calls.some(c=>c.path.endsWith('/sources/x/import') && c.body.count === 3 && c.body.limit === 5)"))
        js("document.querySelector('#source_reach').scrollIntoView()")
        preview('sources-agent-reach')
        viewport = js('({width:innerWidth,height:innerHeight})')
        send('Emulation.setDeviceMetricsOverride', width=560, height=900, deviceScaleFactor=1, mobile=False)
        check('Source cards fit a narrow modal', js("document.querySelector('#mcpmodal .modal').scrollWidth <= document.querySelector('#mcpmodal .modal').clientWidth + 1"))
        preview('sources-narrow')
        send('Emulation.setDeviceMetricsOverride', width=viewport['width'], height=viewport['height'], deviceScaleFactor=1, mobile=False)
        js("document.querySelector('#source_neon [data-source-remove]').click()")
        check('Disconnect affects only the chosen source', wait_for("!sourceUiMock.connectors.neon.configured && sourceUiMock.connectors.n8n.configured"))
        js("document.querySelector('#mcpclose').click()")
    finally:
        js("window.fetch=sourceUiMock.fetch; window.open=sourceUiMock.open; delete window.sourceUiMock")
