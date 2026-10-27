/* Shared CLI workspace: xterm bridges I/O; each native program owns its interaction. */
(() => {
  const el = id => document.getElementById(id);
  const ui = { pid:null, data:null, terminal:null, fit:null, socket:null, session:null, seq:0, retry:null, retries:0,
    accountSeq:0, refreshPending:false, provider:'shell', drafts:{} };
  let mode = localStorage.getItem('oneprod.chatMode');
  let chosenMode = !!mode;
  const error = message => { el('ccerror').textContent = message || ''; el('ccerror').hidden = !message; };
  const status = message => { el('ccstatus').textContent = message; };
  const path = () => `/api/projects/${encodeURIComponent(ui.pid)}/terminal`;
  const adapter = () => ui.data?.providers.find(p => p.id === ui.provider);
  const label = () => adapter()?.label || 'CLI';
  const matchingAccounts = () => (ui.data?.accounts || []).filter(a => a.engine === ui.provider);
  const account = () => ui.data?.accounts.find(a => a.id === el('ccaccount').value);
  const enabledAccounts = () => matchingAccounts().filter(a => a.enabled !== false);
  const readyStatus = () => !adapter()?.installed ? `${label()} is not installed or configured` :
    !adapter()?.needs_account ? 'Ready · uses this PC’s own CLI logins and setup' :
    enabledAccounts().length ? 'Ready · your saved account supplies the login' :
    matchingAccounts().length ? `Your saved ${label()} accounts are turned off` : `No ${label()} account connected in this data folder`;
  const outputText = () => {
    const buffer=ui.terminal?.buffer.active;
    return buffer ? Array.from({length:buffer.length},(_,i)=>buffer.getLine(i)?.translateToString(true) || '').join('\n').trimEnd() : '';
  };

  function setMode(value, remember=true) {
    mode = value;
    if (remember) { chosenMode=true; localStorage.setItem('oneprod.chatMode', value); }
    const code = value === 'code' || value === 'cli';
    document.body.classList.toggle('claude-code-mode', code);
    el('claudecode').hidden = !code;
    el('claudecodetab').setAttribute('aria-selected', String(code));
    el('studiochattab').setAttribute('aria-selected', String(!code));
    el('ccmodehint').textContent = code ? 'Your CLI, inside One Production' :
      ui.session?.running ? 'A CLI is running · end its session to use Studio chat' : 'Production chat and workflows';
    if (ui.pid === state.current) {
      el('input').disabled = !!ui.session?.running || !state.current;
      el('send').disabled = !!ui.session?.running || !state.current || state.busy === state.current;
    }
    if (code && ui.terminal) requestAnimationFrame(() => { fit(); ui.terminal.focus(); });
    if (code && !ui.pid && state.current) project(state.current);
  }

  function fit() {
    if (!ui.terminal || el('claudecode').hidden || el('ccterminal').clientWidth < 60) return;
    ui.fit.fit();
    if (ui.socket?.readyState === WebSocket.OPEN) ui.socket.send(JSON.stringify({t:'resize', rows:Math.max(5,Math.min(250,ui.terminal.rows)), cols:Math.max(20,Math.min(500,ui.terminal.cols))}));
  }

  function disconnect() {
    clearTimeout(ui.retry);
    ui.retry = null;
    const socket = ui.socket; ui.socket = null;
    if (socket) socket.close();
  }

  function makeTerminal() {
    ui.terminal?.dispose();
    el('ccterminal').replaceChildren();
    ui.terminal = new Terminal({cursorBlink:true, fontSize:13, lineHeight:1.25, scrollback:10000,
      fontFamily:'Cascadia Code, Consolas, monospace', allowProposedApi:false,
      theme:{background:'#0b0d12', foreground:'#eef1f7', cursor:'#ff6b4a', selectionBackground:'#3b4bdb80'}});
    ui.fit = new FitAddon.FitAddon();
    ui.terminal.loadAddon(ui.fit);
    // Terminal output must never silently write to the browser clipboard.
    ui.terminal.parser.registerOscHandler(52, () => true);
    ui.terminal.open(el('ccterminal'));
    el('cccopy').hidden=false;
    ui.terminal.onData(data => {
      if (ui.socket?.readyState === WebSocket.OPEN) {
        // Preserve paste framing, including pastes larger than a single transport frame.
        for (let i=0; i<data.length; i+=16000) ui.socket.send(JSON.stringify({t:'input',data:data.slice(i,i+16000)}));
      }
    });
    ui.terminal.attachCustomKeyEventHandler(event => {
      if (event.type === 'keydown' && event.ctrlKey && event.shiftKey && event.code === 'KeyC' && ui.terminal.hasSelection()) {
        navigator.clipboard.writeText(ui.terminal.getSelection()).catch(() => error('Could not copy the selection.'));
        return false;
      }
      if (event.type === 'keydown' && event.ctrlKey && event.shiftKey && event.code === 'KeyV') {
        navigator.clipboard.readText().then(text => ui.terminal.paste(text)).catch(() => error('Paste with the browser menu, or allow clipboard access.'));
        return false;
      }
      return true;
    });
    fit();
  }

  function controls(running) {
    el('ccform').querySelectorAll('input,select,button').forEach(node => node.disabled = running);
    el('ccstart').disabled = running || !adapter()?.installed || (adapter()?.needs_account && (!account() || account().enabled === false));
    el('ccaccount').disabled = running || !enabledAccounts().length;
    el('ccaccounts').disabled = false;
    el('ccrefreshaccounts').disabled = false;
    el('ccend').hidden = !running;
    el('ccreconnect').hidden = !running;
    document.querySelectorAll('[data-cc-key]').forEach(button => button.disabled = !running);
    if (!running) setupHelp();
    if (ui.pid === state.current) {
      el('input').disabled = running || !state.current;
      el('send').disabled = running || !state.current || state.busy === state.current;
    }
  }

  function renderAccountList(data, preferred) {
    const previous = account(), oldSelection = el('ccaccount').value;
    ui.data = data;
    const available = enabledAccounts(), matching = matchingAccounts(), running = ui.session?.running;
    const candidates = [running ? ui.session.account_id : null, preferred, oldSelection, el('picker').value];
    const selected = candidates.find(id => available.some(a => a.id === id)) || available[0]?.id || '';
    const options = matching.map(a => {
      const option = document.createElement('option');
      option.value = a.id; option.textContent = a.label + (a.enabled === false ? ' (turned off)' : '');
      option.disabled = a.enabled === false;
      return option;
    });
    if (!available.length) {
      const placeholder = document.createElement('option');
      placeholder.value = ''; placeholder.textContent = matching.length ? `Turn on a ${label()} account in Accounts` : `No ${label()} accounts connected`;
      options.unshift(placeholder);
    }
    // A running CLI keeps its original identity if that account is subsequently
    // disabled or removed in Accounts. Refreshing the list never restarts it.
    if (running && !options.some(o => o.value === ui.session.account_id)) {
      const option = document.createElement('option'); option.value = ui.session.account_id;
      option.textContent = 'Running account (removed from Accounts)'; option.disabled = true; options.push(option);
    }
    el('ccaccount').replaceChildren(...options);
    el('ccaccount').value = running ? ui.session.account_id : selected;
    if (oldSelection !== el('ccaccount').value && (!el('ccconfig').value || el('ccconfig').value === previous?.config_dir)) {
      el('ccconfig').value = account()?.config_dir || '';
    }
    const notice = el('ccaccountnotice');
    notice.hidden = !adapter()?.needs_account || !!available.length;
    notice.textContent = available.length ? '' : matching.length ?
      `Saved ${label()} accounts are turned off: ${matching.map(a => a.label).join(', ')}. Open Manage accounts and choose Turn on.` :
      `This app instance has no saved ${label()} account.\nOpen Accounts and connect ${label()}. The account is then available in every project. You can also use PowerShell to start a CLI with this PC’s existing login.\nData folder: ${data.data_dir || data.default_cwd}`;
    el('ccaccounts').textContent = matching.length ? 'Manage accounts' : `Add ${label()} account`;
    controls(!!running);
  }

  function rememberDraft() {
    if (!ui.data) return;
    ui.drafts[ui.provider] = {account_id:el('ccaccount').value, cwd:el('cccwd').value,
      config_dir:el('ccconfig').value, studio:el('ccstudio').checked, fresh:el('ccfresh').checked};
  }

  function selectProvider(provider, settings={}) {
    ui.provider = provider;
    el('ccprogram').value = provider;
    const needs = !!adapter()?.needs_account;
    el('cctitle').textContent = label();
    el('ccdescription').textContent = adapter()?.help || 'Choose a CLI to start.';
    el('ccstart').textContent = 'Start ' + label();
    el('ccaccountlabel').textContent = label() + ' account';
    el('ccconfiglabel').textContent = label() + ' setup folder';
    el('cclocalsetup').textContent = `Use this PC’s ${label()} setup`;
    ['ccaccountfield','ccaccounttools','ccconfigfield','ccsetupshortcuts','ccsetuphelp','ccstudiofield','ccfreshfield'].forEach(id => el(id).hidden = !needs);
    el('ccaccount').required = needs;
    el('cccwdhelp').textContent = provider === 'claude' ? 'Choose your workspace to load CLAUDE.md and .claude. Leave blank to use this project’s files.' :
      provider === 'codex' ? 'Choose your workspace to load AGENTS.md and .codex. Leave blank to use this project’s files.' :
      'Starts in this folder with your normal shell profile. Type claude or codex to launch an installed assistant.';
    el('ccstartnote').textContent = needs ? 'Type /help inside the CLI to see its commands.' : 'Uses the logins and configuration already on this PC.';
    el('ccfreshlabel').textContent = provider === 'codex' ? 'New conversation (uncheck to open Codex’s resume picker)' : 'Start a new conversation (keep earlier transcripts)';
    renderAccountList(ui.data, settings.account_id);
    el('cccwd').value = settings.cwd || ui.data.default_cwd;
    el('ccconfig').value = settings.config_dir || account()?.config_dir || '';
    el('ccstudio').checked = needs && settings.studio !== false;
    el('ccfresh').checked = settings.fresh ?? provider === 'codex';
    controls(!!ui.session?.running);
    if (!ui.session?.running) status(readyStatus());
  }

  async function refreshAccounts() {
    if (!ui.pid) return;
    if (!ui.data) { ui.refreshPending=true; return; }
    const pid=ui.pid, projectSeq=ui.seq, requestSeq=++ui.accountSeq;
    try {
      const data = await (await api(path())).json();
      if (ui.pid !== pid || ui.seq !== projectSeq || ui.accountSeq !== requestSeq) return;
      renderAccountList(data);
      if (!ui.session) status(readyStatus());
    } catch(e) { if (ui.pid === pid && ui.accountSeq === requestSeq) error(e.message); }
  }

  function connect(session, retry=false) {
    disconnect();
    ui.session = session;
    ui.terminal.reset();
    const targetPid = ui.pid;
    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${path()}/${session.id}/socket`);
    ui.socket = socket;
    status(retry ? 'Reconnecting…' : `Connecting to ${session.label || label()}…`);
    socket.onopen = () => socket.send(JSON.stringify({t:'auth', token:TOKEN}));
    socket.onmessage = event => {
      if (ui.socket !== socket || ui.pid !== targetPid) return;
      const message = JSON.parse(event.data);
      if (message.t === 'ready') {
        ui.retries = 0; error(''); status(`Connected · ${account()?.label || session.label || label()}`);
        controls(message.running); requestAnimationFrame(() => { fit(); ui.terminal.focus(); });
      } else if (message.t === 'output') ui.terminal.write(message.data);
      else if (message.t === 'error') error(message.message);
      else if (message.t === 'exit') {
        session.running = false; controls(false); status('Session ended · ready to launch another CLI');
        ui.terminal.write('\r\n\x1b[90m[CLI session ended]\x1b[0m\r\n');
        el('ccsetup').open = true;
      } else if (message.t === 'reconnect') socket.close();
    };
    socket.onclose = event => {
      if (ui.socket !== socket || ui.pid !== targetPid || !session.running) return;
      status('Disconnected · your CLI session continues on the server');
      if ([4401,4403,4404].includes(event.code)) {
        error(event.code === 4403 ? 'Unlock One Production to reconnect.' : 'Reconnect through One Production to access this session.');
        return;
      }
      if (ui.retries++ < 5) ui.retry = setTimeout(() => { if (ui.pid === targetPid) connect(session,true); }, Math.min(1000 * 2**ui.retries,10000));
    };
    socket.onerror = () => status('Connection interrupted · reconnecting');
  }

  async function project(pid) {
    const seq = ++ui.seq;
    disconnect(); ui.pid = pid; ui.data = null; ui.session = null; ui.refreshPending=false; ui.drafts={};
    ui.terminal?.dispose(); ui.terminal = null; el('ccterminal').replaceChildren();
    el('cccopy').hidden=true;
    error(''); status('Loading CLI setup…'); el('ccsetup').open = true; controls(false);
    try {
      const data = await (await api(path())).json();
      if (ui.seq !== seq || state.current !== pid) return;
      const saved = data.preferences, active = data.session?.running ? data.session : null;
      ui.session = active;
      ui.data = data;
      el('ccprogram').replaceChildren(...data.providers.map(p => {
        const option = document.createElement('option'); option.value=p.id; option.textContent=p.label+(p.installed ? '' : ' (not installed)'); return option;
      }));
      const provider = active?.provider || saved.provider || (saved.account_id ? 'claude' : 'shell');
      selectProvider(provider, active || saved.providers?.[provider] || saved);
      el('ccsummary').textContent = active?.cwd || '';
      setupHelp(); controls(!!active);
      if (!chosenMode) setMode(active || enabledAccounts().length ? 'cli' : 'studio',false);
      if (active) { makeTerminal(); el('ccsetup').open = false; connect(active); }
      else status(readyStatus());
      if (ui.refreshPending) { ui.refreshPending=false; refreshAccounts(); }
    } catch (e) { if (ui.seq === seq) { error(e.message); status('Could not load CLI setup'); } }
  }

  function setupHelp() {
    const current = account();
    el('cclocalsetup').disabled = !current?.can_share_setup;
    el('ccsavedsetup').disabled = !current || !!ui.session?.running;
    el('ccsetuphelp').textContent = !current ? `Connect or turn on a ${label()} account in Accounts to choose its setup folder.` : current.can_share_setup ?
      'The saved account supplies authentication. This folder supplies settings, skills, plugins and native session history.' :
      'This browser-login account uses its own setup folder to preserve the selected identity.';
  }

  el('ccaccount').onchange = () => { el('ccconfig').value = account()?.config_dir || ''; setupHelp(); };
  el('ccprogram').onchange = () => {
    if (ui.session?.running) return;
    rememberDraft(); error('');
    selectProvider(el('ccprogram').value, ui.drafts[el('ccprogram').value] || ui.data.preferences.providers?.[el('ccprogram').value] || {});
  };
  el('ccaccounts').onclick = () => openAccounts();
  el('ccrefreshaccounts').onclick = refreshAccounts;
  document.addEventListener('oneprod:accounts-changed', refreshAccounts);
  el('ccsavedsetup').onclick = () => { el('ccconfig').value = account()?.config_dir || ''; };
  el('cclocalsetup').onclick = () => { el('ccconfig').value = adapter()?.local_config_dir || ''; };
  el('claudecodetab').onclick = () => setMode('cli');
  el('studiochattab').onclick = () => setMode('studio');
  el('ccreconnect').onclick = () => { if (ui.session) { ui.retries=0; connect(ui.session); } };
  el('cccopy').onclick = () => navigator.clipboard.writeText(outputText()).then(()=>toast('Terminal output copied')).catch(()=>error('Could not copy terminal output.'));
  el('ccform').onsubmit = async event => {
    event.preventDefault(); if (!ui.pid || (adapter()?.needs_account && !account())) return;
    const pid = ui.pid, seq = ui.seq;
    error(''); el('ccstart').disabled = true; status(`Starting ${label()}…`); rememberDraft();
    try {
      const response = await api(path(), {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
        provider:ui.provider, account_id:adapter().needs_account ? account().id : null, cwd:el('cccwd').value.trim(), config_dir:adapter().needs_account ? el('ccconfig').value.trim() : '',
        studio:el('ccstudio').checked, fresh:el('ccfresh').checked, rows:32, cols:100})});
      const session = await response.json();
      if (ui.pid !== pid || ui.seq !== seq) return;
      controls(true); el('ccsetup').open = false; el('ccsummary').textContent = session.cwd;
      makeTerminal(); connect(session);
    } catch (e) { if (ui.pid === pid) { controls(false); setupHelp(); error(e.message); status(`Could not start ${label()}`); } }
  };
  el('ccend').onclick = async () => {
    if (!ui.session) return;
    const pid=ui.pid, session=ui.session; el('ccend').disabled=true;
    try {
      await api(`${path()}/${session.id}`,{method:'DELETE'});
      if (ui.pid !== pid) return;
      session.running=false; disconnect(); controls(false); setupHelp(); el('ccsetup').open=true; status('Session ended · transcripts kept');
      loadFiles();
    } catch(e) { error(e.message); }
    finally { el('ccend').disabled=false; }
  };
  document.querySelectorAll('[data-cc-key]').forEach(button => button.onclick = () => {
    const data={interrupt:'\x03',escape:'\x1b',mode:'\x1b[Z'}[button.dataset.ccKey];
    if (ui.socket?.readyState === WebSocket.OPEN) ui.socket.send(JSON.stringify({t:'input',data}));
    ui.terminal?.focus();
  });
  new ResizeObserver(() => requestAnimationFrame(fit)).observe(el('ccterminal'));
  window.addEventListener('beforeunload',disconnect);
  window.cliTerminal = {project, setMode, outputText};
  window.claudeCode = window.cliTerminal; // old project links and browser helpers
  setMode(mode || 'studio',false);
  if (state.current) project(state.current);
})();
