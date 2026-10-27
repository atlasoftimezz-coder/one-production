// Source cards share the existing MCP settings and Drive account. Secrets are never read back.
const sourcesSection = document.createElement('section');
sourcesSection.id = 'source_connections';
sourcesSection.innerHTML = `<div class="sect" style="padding:0 0 8px">Your sources · Claude and Codex</div>
  <div id="sources_error" class="drive-result error" role="status" hidden></div>
  <div class="source-grid">
    ${[['n8n','n8n','Run your connected automation workflows.'],['nexlev','Nexlev','Channel research, analytics and transcripts.'],['neon','Neon','Query your databases and research records.']].map(([name,title,description]) => `
      <section class="stylecard source-card" id="source_${name}" aria-label="${title} connection">
        <div class="source-title"><h4>${title}</h4><span class="tst" data-source-state>Loading…</span></div>
        <p class="stnote">${description}</p>
        <details><summary>Configure connection</summary><div>
          <form data-source-form="${name}">
            <label>MCP server URL <input data-source-url type="url" required placeholder="https://your-server/mcp"></label>
            <label>Sign-in method <select data-source-auth><option value="header">API token</option><option value="oauth">Browser sign-in</option></select></label>
            <label data-source-token-label>API token <input data-source-token type="password" autocomplete="off" placeholder="Leave blank to keep a saved token"></label>
            <label class="source-toggle"><input type="checkbox" data-source-enabled checked> Enable this source</label>
            <p class="stnote" data-source-help></p>
            <div class="drive-actions"><button class="btn">Save</button><button type="button" class="btn" data-source-remove>Disconnect</button></div>
          </form>
        </div></details>
        <div class="drive-actions"><button class="btn primary" data-source-login hidden>Sign in</button><button class="btn" data-source-test disabled>Test connection</button></div>
        <p class="drive-result source-result" role="status" data-source-result hidden></p>
        <a class="stnote" data-source-signin target="_blank" rel="noopener noreferrer" hidden>Open sign-in in a new tab</a>
      </section>`).join('')}
    <section class="stylecard source-card" id="source_discord" aria-label="Discord connection">
      <div class="source-title"><h4>Discord</h4><span class="tst" data-source-state>Loading…</span></div>
      <p class="stnote">Read final scripts through your existing n8n workflow. This reader does not send, edit or delete Discord messages.</p>
      <details><summary>Configure script reader</summary><div><form id="discord_form">
        <label>n8n MCP server name <input id="discord_server" value="n8n" required></label>
        <label>Workflow ID <input id="discord_workflow" required></label>
        <label class="source-toggle"><input type="checkbox" id="discord_enabled"> Enable Discord source</label>
        <div><button class="btn">Save reader</button></div>
      </form></div></details>
      <div class="drive-actions"><button class="btn" id="discord_test">Test workflow</button><button class="btn primary" id="discord_read">Read scripts</button><button class="btn" id="discord_import" disabled>Import into project</button></div>
      <p class="drive-result source-result" id="discord_result" role="status" hidden></p>
    </section>
    <section class="stylecard source-card" id="source_reach" aria-label="Agent Reach X source">
      <div class="source-title"><h4>Agent Reach · X accounts</h4><span class="tst" data-source-state>Loading…</span></div>
      <p class="stnote">Collect recent posts from the accounts you follow. X support in Agent Reach is experimental and needs your login. Save handles below; it does not automatically import your Following list.</p>
      <form id="reach_form">
        <label>X accounts <textarea id="reach_handles" rows="3" placeholder="@account_one&#10;https://x.com/account_two"></textarea></label>
        <details><summary>Use an existing Agent Reach installation</summary><div><label>Executable path (optional) <input id="reach_command" placeholder="Auto-detect, or choose the full path to agent-reach.exe"></label></div></details>
        <label class="source-toggle"><input type="checkbox" id="reach_enabled"> Enable this source for agents</label>
        <div><button class="btn">Save accounts</button></div>
      </form>
      <div class="drive-actions"><button class="btn" data-reach-action="install">Install core</button><button class="btn" data-reach-action="enable_x">Set up X</button><button class="btn" data-reach-action="login">Sign in to X</button><button class="btn" id="reach_check">Check status</button><button class="btn primary" id="reach_collect">Collect posts into project</button></div>
      <p class="stnote">Install core uses a separate Python runtime from the official GitHub source. Set up X may download browser tools. Sign in yourself in its browser. <a href="https://github.com/Panniantong/Agent-Reach" target="_blank" rel="noopener noreferrer">Project and setup guide</a></p>
      <p class="drive-result source-result" id="reach_result" role="status" hidden></p>
    </section>
  </div>`;
document.querySelector('#mcpmodal .drive-connector').insertAdjacentElement('afterend', sourcesSection);

let sourcesState = null, discordExecution = null, reachOffset = 0;
const sourceLoginJobs = new Map();
const sourceWait = ms => new Promise(resolve => setTimeout(resolve, ms));
const sourceApi = async (path, options = {}) => (await api(path, options)).json();
const sourceJson = body => ({method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});

function sourceMessage(element, message, error = false) {
  element.hidden = false; element.className = 'drive-result source-result' + (error ? ' error' : ''); element.textContent = message;
}

async function loadSources(fill = true) {
  try {
    sourcesState = await sourceApi('/api/sources'); $('#sources_error').hidden = true;
    for (const [name, cfg] of Object.entries(sourcesState.connectors)) {
      const card = $(`#source_${name}`), badge = card.querySelector('[data-source-state]');
      badge.className = 'tst' + (cfg.signed_in ? ' done' : '');
      badge.textContent = !cfg.configured ? 'Not connected' : !cfg.enabled ? 'Disabled' : cfg.signed_in ? 'Signed in' : 'Saved · test access';
      if (fill) {
        card.querySelector('[data-source-url]').value = cfg.url;
        card.querySelector('[data-source-auth]').value = cfg.auth;
        card.querySelector('[data-source-enabled]').checked = cfg.enabled || !cfg.configured;
        card.querySelector('[data-source-token]').value = '';
      }
      card.querySelector('[data-source-help]').textContent = cfg.help;
      card.querySelector('[data-source-test]').disabled = !cfg.configured || !cfg.enabled;
      card.querySelector('[data-source-remove]').disabled = !cfg.configured;
      sourceAuthFields(card);
    }
    const d = sourcesState.discord, r = sourcesState.agent_reach;
    $('#source_discord [data-source-state]').textContent = d.enabled ? 'Enabled · test workflow' : 'Not connected';
    $('#source_reach [data-source-state]').textContent = !r.installed ? 'Not installed' : r.enabled ? 'Installed · check X' : 'Disabled';
    if (fill) {
      $('#discord_server').value = d.server; $('#discord_workflow').value = d.workflow_id; $('#discord_enabled').checked = d.enabled;
      $('#reach_handles').value = r.handles.join('\n'); $('#reach_command').value = r.command; $('#reach_enabled').checked = r.enabled;
    }
    $('#discord_test').disabled = !d.enabled; $('#discord_read').disabled = !d.enabled;
    $('#reach_check').disabled = !r.installed; $('#reach_collect').disabled = !r.installed || !r.enabled || !r.handles.length;
    $('[data-reach-action=install]').disabled = r.installed;
    $('[data-reach-action=enable_x]').disabled = !r.installed; $('[data-reach-action=login]').disabled = !r.installed;
  } catch (e) { sourceMessage($('#sources_error'), e.message, true); }
}

function sourceAuthFields(card) {
  const oauth = card.querySelector('[data-source-auth]').value === 'oauth';
  card.querySelector('[data-source-token-label]').hidden = oauth;
  card.querySelector('[data-source-login]').hidden = !oauth;
}

async function saveSource(name) {
  const card = $(`#source_${name}`);
  await sourceApi(`/api/sources/connectors/${name}`, sourceJson({url:card.querySelector('[data-source-url]').value.trim(),
    auth:card.querySelector('[data-source-auth]').value, token:card.querySelector('[data-source-token]').value,
    enabled:card.querySelector('[data-source-enabled]').checked}));
  card.querySelector('[data-source-token]').value = '';
  await loadSources(false); await loadMcp();
}

sourcesSection.querySelectorAll('[data-source-form]').forEach(form => {
  const name = form.dataset.sourceForm, card = $(`#source_${name}`), result = card.querySelector('[data-source-result]');
  card.querySelector('[data-source-auth]').onchange = () => sourceAuthFields(card);
  form.onsubmit = async e => {
    e.preventDefault(); const button = form.querySelector('button'); button.disabled = true;
    try { await saveSource(name); sourceMessage(result, 'Saved. ' + (card.querySelector('[data-source-auth]').value === 'oauth' ? 'Press Sign in to authorize this source.' : 'Press Test connection to verify access.')); }
    catch (err) { sourceMessage(result, err.message, true); }
    finally { button.disabled = false; }
  };
  card.querySelector('[data-source-test]').onclick = async e => {
    e.target.disabled = true; sourceMessage(result, 'Connecting and listing available tools…');
    try {
      const tested = await sourceApi(`/api/sources/connectors/${name}/test`, {method:'POST'});
      sourceMessage(result, `Connection verified · ${tested.tools.length} tools\n${tested.tools.slice(0,25).join(', ')}`);
      card.querySelector('[data-source-state]').textContent = 'Verified';
      card.querySelector('[data-source-state]').className = 'tst done';
    } catch (err) { sourceMessage(result, err.message, true); card.querySelector('[data-source-state]').textContent = 'Test failed'; card.querySelector('[data-source-state]').className = 'tst failed'; }
    finally { e.target.disabled = false; }
  };
  card.querySelector('[data-source-remove]').onclick = async () => {
    try { await sourceApi(`/api/sources/connectors/${name}`, {method:'DELETE'}); sourceMessage(result, 'Disconnected from One Production.'); await loadSources(); await loadMcp(); }
    catch (err) { sourceMessage(result, err.message, true); }
  };
  card.querySelector('[data-source-login]').onclick = async e => {
    const button = e.target; button.disabled = true;
    const tab = window.open('about:blank', '_blank'); if (tab) tab.opener = null;
    let opened = false, job;
    try {
      await saveSource(name); sourceMessage(result, 'Starting browser sign-in…');
      job = await sourceApi(`/api/sources/connectors/${name}/login`, {method:'POST'}); sourceLoginJobs.set(name, job.id);
      for (let n=0; n<310; n++) {
        if (sourceLoginJobs.get(name) !== job.id) return;
        const status = await sourceApi(`/api/sources/login/${job.id}`);
        if (status.url) {
          const link = card.querySelector('[data-source-signin]'); link.href = status.url; link.hidden = false;
          if (!opened && tab && !tab.closed) { tab.location.replace(status.url); opened = true; }
          sourceMessage(result, 'Complete the source’s sign-in in the new tab. Use the sign-in link here if your browser blocked it.');
        }
        if (status.state === 'connected') { sourceMessage(result, `Connected · ${(status.tools || []).length} tools available.`); await loadSources(false); return; }
        if (['failed','cancelled'].includes(status.state)) throw new Error(status.error || 'Sign-in cancelled.');
        await sourceWait(1000);
      }
      throw new Error('Sign-in timed out. Try again.');
    } catch (err) { sourceMessage(result, err.message, true); }
    finally {
      if (job) { sourceLoginJobs.delete(name); await sourceApi(`/api/sources/login/${job.id}`, {method:'DELETE'}).catch(() => {}); }
      if (tab && !opened && !tab.closed) tab.close();
      card.querySelector('[data-source-signin]').hidden = true; button.disabled = false;
    }
  };
});

$('#discord_form').onsubmit = async e => {
  e.preventDefault();
  try {
    await sourceApi('/api/sources/preferences/discord', sourceJson({server:$('#discord_server').value.trim(), workflow_id:$('#discord_workflow').value.trim(), enabled:$('#discord_enabled').checked}));
    discordExecution = null; $('#discord_import').disabled = true;
    sourceMessage($('#discord_result'), 'Reader saved. Test workflow verifies its configuration without reading Discord.'); await loadSources(false);
  } catch (err) { sourceMessage($('#discord_result'), err.message, true); }
};
$('#discord_test').onclick = async e => {
  e.target.disabled = true;
  try { const r = await sourceApi('/api/sources/discord/test', {method:'POST'}); sourceMessage($('#discord_result'), `${r.workflow.name}\n${r.message}`); }
  catch (err) { sourceMessage($('#discord_result'), err.message, true); }
  finally { e.target.disabled = false; }
};
$('#discord_read').onclick = async e => {
  e.target.disabled = true; $('#discord_import').disabled = true; sourceMessage($('#discord_result'), 'Reading scripts through n8n…');
  try {
    const started = await sourceApi('/api/sources/discord/read', {method:'POST'}); discordExecution = started.executionId;
    for (let n=0; n<45; n++) {
      const result = await sourceApi(`/api/sources/discord/executions/${encodeURIComponent(discordExecution)}`);
      const status = result.execution?.status;
      if (status === 'success') {
        const messages = result.messages || [];
        const text = messages.length ? messages.map(m => [m.timestamp, m.author].filter(Boolean).join(' · ') + '\n' + (m.content || '')
          + (m.attachments?.length ? '\nAttachments: ' + m.attachments.map(a => a.name || a.url).join(', ') : '') + '\n' + m.source_url).join('\n\n') : 'The reader returned no script messages.';
        sourceMessage($('#discord_result'), `${messages.length} script messages\n\n${text}`); $('#discord_import').disabled = !state.current; return;
      }
      if (['error','crashed','canceled'].includes(status) || result.error) throw new Error(result.error || `Workflow ${status}. Check n8n execution details.`);
      await sourceWait(2000);
    }
    throw new Error('The workflow is still running. Check it in n8n before starting another read.');
  } catch (err) { sourceMessage($('#discord_result'), err.message, true); }
  finally { e.target.disabled = false; }
};
$('#discord_import').onclick = async e => {
  if (!state.current || !discordExecution) return;
  e.target.disabled = true;
  try {
    const result = await sourceApi(`/api/projects/${state.current}/sources/discord/import`, {...sourceJson({execution_id:discordExecution}), method:'POST'});
    sourceMessage($('#discord_result'), `Imported ${result.file}. Open it from this project’s Files panel.`); loadFiles();
  } catch (err) { sourceMessage($('#discord_result'), err.message, true); }
};

$('#reach_form').onsubmit = async e => {
  e.preventDefault();
  try {
    await sourceApi('/api/sources/preferences/agent_reach', sourceJson({command:$('#reach_command').value.trim(), handles:$('#reach_handles').value, enabled:$('#reach_enabled').checked}));
    reachOffset = 0; sourceMessage($('#reach_result'), 'X accounts saved. Set up X and sign in, then check status.'); await loadSources(false);
  } catch (err) { sourceMessage($('#reach_result'), err.message, true); }
};
sourcesSection.querySelectorAll('[data-reach-action]').forEach(button => button.onclick = async () => {
  button.disabled = true;
  try {
    let job = await sourceApi(`/api/sources/agent-reach/actions/${button.dataset.reachAction}`, {method:'POST'});
    while (job.state === 'running') { sourceMessage($('#reach_result'), job.message); await sourceWait(1500); job = await sourceApi(`/api/sources/agent-reach/jobs/${job.id}`); }
    sourceMessage($('#reach_result'), job.message, job.state !== 'done'); await loadSources(false);
  } catch (err) { sourceMessage($('#reach_result'), err.message, true); }
  finally { button.disabled = false; }
});
$('#reach_check').onclick = async e => {
  e.target.disabled = true; sourceMessage($('#reach_result'), 'Checking the local Agent Reach setup…');
  try { sourceMessage($('#reach_result'), JSON.stringify(await sourceApi('/api/sources/agent-reach/check', {method:'POST'}), null, 2)); }
  catch (err) { sourceMessage($('#reach_result'), err.message, true); }
  finally { e.target.disabled = false; }
};
$('#reach_collect').onclick = async e => {
  if (!state.current) return sourceMessage($('#reach_result'), 'Choose a project before collecting posts.', true);
  e.target.disabled = true; sourceMessage($('#reach_result'), 'Collecting posts from saved accounts. X may take a moment between requests…');
  try {
    const result = await sourceApi(`/api/projects/${state.current}/sources/x/import`, {...sourceJson({offset:reachOffset, count:3, limit:5}), method:'POST'});
    reachOffset = result.next_offset ?? 0;
    sourceMessage($('#reach_result'), `Saved posts from ${result.accounts.length} account${result.accounts.length === 1 ? '' : 's'} to ${result.file}.` + (result.next_offset != null ? '\nPress Collect again for the next batch.' : '\nAll saved accounts have been covered.'));
    loadFiles();
  } catch (err) { sourceMessage($('#reach_result'), err.message, true); }
  finally { e.target.disabled = false; }
};
