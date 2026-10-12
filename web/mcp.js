// MCP page (owner, 2026-10-12): the outside MCP servers the studio's Claude agent may use - add custom ones
// (a local program over stdio, or a remote http / sse server with a token header), switch them on/off, and
// Test: connect and list the server's tools. Every tool call still shows an approval card in chats.
const mcpModal = document.createElement('div');
mcpModal.id = 'mcpmodal';
mcpModal.className = 'scrim';
mcpModal.innerHTML = `<div class="modal wide" role="dialog" aria-label="MCP servers">
  <div class="mhead"><div style="flex:1"><h3>MCP servers</h3><p>Outside tools for Claude chats and episodes: a program on this PC (stdio) or a server on the web (http / sse). Every call is shown to you first; writing, deleting, running programs or uploading is asked every time. Header values (tokens) go to Windows Credential Manager; a stdio server's environment values are saved in the app's settings file.</p></div><button class="iconbtn" id="mcpclose" aria-label="Close MCP servers">×</button></div>
  <div style="padding:20px;max-height:75vh;overflow:auto;display:grid;gap:16px">
    <div><div class="sect" style="padding:0 0 6px">Connected servers</div><div id="mcp_list" aria-live="polite"></div></div>
    <form id="mcp_form" class="stylecard" style="display:grid;gap:8px">
      <b>Add a server</b>
      <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:end">
        <label>Name <input id="mcp_name" maxlength="32" required placeholder="e.g. drive" style="width:10em" pattern="[a-z][a-z0-9_\\-]{0,31}" title="lower-case letters, digits, - and _"></label>
        <label>Kind <select id="mcp_type"><option value="stdio">Program on this PC (stdio)</option><option value="http">Web server (http)</option><option value="sse">Web server (sse)</option></select></label>
        <label style="flex:1;min-width:220px" id="mcp_target_l">Command <input id="mcp_target" required placeholder="npx"></label></div>
      <div id="mcp_stdio" style="display:flex;gap:12px;flex-wrap:wrap">
        <label style="flex:2;min-width:220px">Arguments (space separated; "quote" ones with spaces) <input id="mcp_args" placeholder="-y @modelcontextprotocol/server-filesystem D:\\Media"></label>
        <label style="flex:1;min-width:200px">Environment (NAME=value, one per line) <textarea id="mcp_env" rows="2" placeholder="API_KEY=..."></textarea></label></div>
      <div id="mcp_http" style="display:none;gap:12px;flex-wrap:wrap">
        <label style="flex:1;min-width:160px">Header name <input id="mcp_hname" placeholder="Authorization"></label>
        <label style="flex:2;min-width:220px">Header value (kept in Credential Manager) <input id="mcp_hval" type="password" autocomplete="off" placeholder="Bearer ..."></label></div>
      <div><button class="btn primary">Add and test</button></div>
      <p class="stnote">Only add servers you trust: a stdio server runs as a program on this PC. Popular ones: a filesystem server for a media drive, Desktop Commander, a Google Drive or Notion server (they bring their own sign-in).</p>
    </form>
  </div></div>`;
document.body.append(mcpModal);
$('#mcpclose').onclick = () => mcpModal.classList.remove('open');
document.addEventListener('keydown', e => { if (e.key === 'Escape') mcpModal.classList.remove('open'); });

let mcpSaved = {};
const mcpTests = {};

function openMcp() { mcpModal.classList.add('open'); loadMcp(); }

$('#mcp_type').onchange = () => {
  const stdio = $('#mcp_type').value === 'stdio';
  $('#mcp_stdio').style.display = stdio ? 'flex' : 'none';
  $('#mcp_http').style.display = stdio ? 'none' : 'flex';
  $('#mcp_target_l').firstChild.textContent = stdio ? 'Command ' : 'URL ';
  $('#mcp_target').placeholder = stdio ? 'npx' : 'https://example.com/mcp';
};

async function loadMcp() {
  try { mcpSaved = (await (await api('/api/mcp-servers')).json()).servers || {}; } catch (e) { mcpSaved = {}; }
  const names = Object.keys(mcpSaved);
  const box = $('#mcp_list');
  box.innerHTML = names.length ? names.map(n => {
    const s = mcpSaved[n], on = s.enabled !== false, t = mcpTests[n];
    const what = s.url || [s.command, ...(s.args || [])].join(' ');
    const test = !t ? '' : t.busy ? '<span class="tst running">testing…</span>'
      : t.ok ? `<span class="tst done">works · ${t.tools.length} tools</span><div class="stnote" style="margin-top:4px">${t.tools.slice(0, 30).map(x => `<span class="stchip">${esc(x)}</span>`).join(' ')}</div>`
      : `<span class="tst failed">failed</span> <span class="stnote">${esc(t.error || '')}</span>`;
    return `<div class="stylecard" data-mcp="${esc(n)}">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <b>${esc(n)}</b><span class="stchip">${esc(s.type)}</span>${(s.headers || []).map(h => `<span class="stchip" title="value in Credential Manager">${esc(h)} 🔒</span>`).join('')}
        <code style="flex:1;min-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(what)}</code>
        <label style="display:flex;gap:4px;align-items:center"><input type="checkbox" data-mcpon ${on ? 'checked' : ''}> on</label>
        <button class="btn" data-mcptest>Test</button>
        <button class="iconbtn" data-mcpdel title="Remove ${esc(n)}" aria-label="Remove ${esc(n)}">✕</button></div>
      ${test ? `<div style="margin-top:6px">${test}</div>` : ''}</div>`;
  }).join('') : '<div class="navempty">No outside servers yet - add one below.</div>';
  box.querySelectorAll('[data-mcp]').forEach(card => {
    const n = card.dataset.mcp;
    card.querySelector('[data-mcpdel]').onclick = async () => {
      const next = { ...mcpSaved }; delete next[n]; delete mcpTests[n]; await saveMcpServers(next);
    };
    card.querySelector('[data-mcpon]').onchange = async e => {
      const next = { ...mcpSaved, [n]: { ...mcpSaved[n], enabled: e.target.checked } };
      if (e.target.checked) delete next[n].enabled;
      await saveMcpServers(next);
    };
    card.querySelector('[data-mcptest]').onclick = () => testMcp(n);
  });
}

async function saveMcpServers(servers) {
  try {
    await api('/api/mcp-servers', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ servers }) });
    toast('Saved - new Claude chats use it');
  } catch (e) { toast(e.message, true); }
  await loadMcp();
}

async function testMcp(n) {
  mcpTests[n] = { busy: true }; loadMcp();
  try { mcpTests[n] = await (await api(`/api/mcp-servers/${encodeURIComponent(n)}/test`, { method: 'POST' })).json(); }
  catch (e) { mcpTests[n] = { ok: false, error: e.message }; }
  loadMcp();
}

$('#mcp_form').onsubmit = async e => {
  e.preventDefault();
  const n = $('#mcp_name').value.trim(), type = $('#mcp_type').value, target = $('#mcp_target').value.trim();
  if (mcpSaved[n]) return toast(`There is already a server called ${n}`, true);
  let entry;
  if (type === 'stdio') {
    const env = {};
    for (const line of $('#mcp_env').value.split(/\r?\n/)) {
      const i = line.indexOf('=');
      if (i > 0) env[line.slice(0, i).trim()] = line.slice(i + 1).trim();
    }
    // "quoted arguments" keep their spaces (Windows paths: D:\YT project\...)
    const args = ($('#mcp_args').value.match(/"[^"]*"|\S+/g) || []).map(a => a.replace(/^"(.*)"$/, '$1'));
    entry = { type, command: target, args };
    if (Object.keys(env).length) entry.env = env;
  } else {
    entry = { type, url: target };
    const h = $('#mcp_hname').value.trim();
    if (h) entry.headers = { [h]: $('#mcp_hval').value };
  }
  await saveMcpServers({ ...mcpSaved, [n]: entry });
  if (mcpSaved[n]) {
    ['#mcp_name', '#mcp_target', '#mcp_args', '#mcp_env', '#mcp_hname', '#mcp_hval'].forEach(id => { $(id).value = ''; });
    testMcp(n);
  }
};

$('#mcpbtn').onclick = openMcp;
