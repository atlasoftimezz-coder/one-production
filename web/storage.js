// Storage page (owner, 2026-10-12): finished episodes copied to an internal folder and/or Google Drive (the
// owner's Drive API client, their 5 TB plan), and files imported from either into the current project.
const storModal = document.createElement('div');
storModal.id = 'stormodal';
storModal.className = 'scrim';
storModal.innerHTML = `<div class="modal wide" role="dialog" aria-label="Storage">
  <div class="mhead"><div style="flex:1"><h3 id="stor_title">Storage</h3><p id="stor_description">Where finished episodes are copied, and where you can pull files into a project from. Projects keep working on this PC (fast for Resolve); storage gets a copy.</p></div><button class="iconbtn" id="storclose" aria-label="Close Storage">×</button></div>
  <div style="padding:20px;max-height:78vh;overflow:auto;display:grid;gap:16px">
    <div id="stor_grid">
      <form class="stylecard" id="stor_int" style="gap:8px;margin:0">
        <div style="display:flex;gap:8px;align-items:center"><b style="flex:1">Internal storage</b><span id="stor_int_state"></span></div>
        <label>Folder <input id="stor_path" placeholder="E:\\One Production exports"></label>
        <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="stor_int_auto"> Copy every finished episode here</label>
        <div><button class="btn primary">Save</button></div>
        <p class="stnote">Any folder this PC can write to: another drive, an external disk or a network share.</p>
      </form>
      <form class="stylecard" id="stor_drv" style="display:grid;gap:8px;margin:0">
        <div style="display:flex;gap:8px;align-items:center"><b style="flex:1">Google Drive</b><span id="stor_drv_state"></span></div>
        <details class="drive-setup" id="stor_drive_setup">
          <summary>Google Cloud setup</summary>
          <div class="drive-fields">
            <ol class="drive-steps">
              <li>In <a href="https://console.cloud.google.com/apis/library/drive.googleapis.com" target="_blank" rel="noopener noreferrer">Google Cloud</a>, enable the Google Drive API for your project.</li>
              <li>Configure the OAuth consent screen. If the app is in Testing, add your Google email as a test user.</li>
              <li>Create an OAuth client with application type <b>Desktop app</b>. Download its JSON file and choose it below, or paste the client ID and secret.</li>
            </ol>
            <label>OAuth client JSON <input id="stor_client_json" type="file" accept=".json,application/json"></label>
            <p class="stnote">The file fills in the fields below. Settings are saved only when you press Save or Connect.</p>
            <label>OAuth client ID <input id="stor_cid" autocomplete="off" placeholder="....apps.googleusercontent.com"></label>
            <label>Client secret <input id="stor_secret" type="password" autocomplete="off" placeholder="Leave empty to keep your saved secret"></label>
            <p class="stnote">Your client secret and Google login are kept in Windows Credential Manager.</p>
          </div>
        </details>
        <details class="drive-setup"><summary>Optional: copy finished episodes to Drive</summary><div class="drive-fields">
          <label>Export folder in your Drive <input id="stor_folder" value="One Production" maxlength="120"></label>
          <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="stor_drv_auto"> Copy every finished episode to Drive</label>
        </div></details>
        <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn">Save</button>
          <button type="button" class="btn primary" id="stor_connect">Connect Google Drive</button>
          <button type="button" class="btn" id="stor_drive_test" disabled>Test connection</button>
          <button type="button" class="btn" id="stor_disconnect" style="display:none">Disconnect</button></div>
        <p class="stnote">Google will ask to read your Drive and create or upload files opened or created with this app. Copying finished episodes is optional.</p>
        <p id="stor_drive_result" class="drive-result" role="status" hidden></p>
        <p id="stor_signin_fallback" class="drive-result" hidden><a id="stor_signin_link" target="_blank" rel="noopener noreferrer">Open Google sign-in</a> if the new tab did not open. Keep this window open while you sign in.</p>
      </form>
    </div>
    <div class="stylecard" id="stor_browser" style="margin:0">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b style="flex:1">Import into this project</b>
        <select id="stor_loc"><option value="internal">Internal storage</option><option value="drive">Google Drive</option></select>
        <button class="btn" id="stor_up" title="Up one folder">↑ Up</button>
        <button class="btn primary" id="stor_import" disabled>Import selected</button></div>
      <div class="stnote" id="stor_where" style="margin:6px 0"></div>
      <p class="stnote" id="stor_selection" aria-live="polite"></p>
      <div id="stor_items" style="display:grid;gap:2px;max-height:300px;overflow:auto"></div>
      <p class="drive-result" id="stor_feedback" role="status" hidden></p>
    </div>
    <div id="stor_copies"><div class="sect" style="padding:0 0 6px">Copies</div><div id="stor_jobs" aria-live="polite"></div></div>
  </div></div>`;
document.body.append(storModal);
$('#storclose').onclick = closeStorage;
document.addEventListener('keydown', e => { if (e.key === 'Escape' && storModal.classList.contains('open')) closeStorage(); });

let storState = null, storTimer = null, storTrail = [];
let storReturnToMcp = false, storBrowseOnOpen = false, storBrowseRequest = 0, storConnectAttempt = 0;
let storConnecting = false, storImporting = false;
let storNormalLocation = 'internal';
const storPicked = new Set();
const sizeOf = b => b == null ? '' : b > 1e9 ? (b / 1e9).toFixed(1) + ' GB' : b > 1e6 ? (b / 1e6).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1e3)) + ' KB';

function closeStorage() {
  storModal.classList.remove('open'); clearInterval(storTimer);
  storConnectAttempt++; storConnecting = false; storBrowseRequest++;
  $('#stor_connect').disabled = false; $('#stor_signin_fallback').hidden = true;
  const back = storReturnToMcp; storReturnToMcp = false;
  if (back) openMcp();
}

async function openStorage(options = {}) {
  const drive = options.drive === true;
  storModal.classList.toggle('drive-focused', drive);
  storModal.querySelector('[role="dialog"]').setAttribute('aria-label', drive ? 'Google Drive' : 'Storage');
  $('#stor_title').textContent = drive ? 'Google Drive' : 'Storage';
  $('#stor_description').textContent = drive
    ? 'Connect your account and import competitor videos, transcripts and reference files into a project.'
    : 'Where finished episodes are copied, and where you can pull files into a project from. Projects keep working on this PC (fast for Resolve); storage gets a copy.';
  storReturnToMcp = options.returnToMcp === true; storBrowseOnOpen = options.browse === true;
  $('#stor_loc').value = drive ? 'drive' : storNormalLocation;
  $('#stor_drive_setup').open = !storState?.drive.connected;
  $('#stor_drive_result').hidden = true; $('#stor_feedback').hidden = true;
  storModal.classList.add('open');
  await loadStorage(true);
  clearInterval(storTimer);
  if (storModal.classList.contains('open')) {
    storTimer = setInterval(() => { if (storModal.classList.contains('open')) loadStorage(false); }, 3000);
    (storBrowseOnOpen ? $('#stor_browser') : $('#stor_drv')).scrollIntoView({ block: 'nearest' });
    (storBrowseOnOpen ? $('#stor_up') : $('#stor_connect')).focus({ preventScroll: true });
  }
}

async function loadStorage(fill) {
  try { storState = await (await api('/api/storage')).json(); } catch (e) { return toast(e.message, true); }
  const i = storState.internal, d = storState.drive;
  if (fill) {
    $('#stor_path').value = i.path || ''; $('#stor_int_auto').checked = !!i.auto;
    $('#stor_cid').value = d.client_id || ''; $('#stor_folder').value = d.folder || 'One Production';
    $('#stor_drv_auto').checked = !!d.auto; $('#stor_secret').value = '';
    $('#stor_drive_setup').open = !d.client_id || (!d.connected && !storBrowseOnOpen);
    storTrail = []; storPicked.clear();
    if ($('#stor_loc').value === 'drive' && !d.connected) {
      storBrowseRequest++; $('#stor_up').disabled = true; $('#stor_where').textContent = 'My Drive';
      $('#stor_items').innerHTML = '<div class="navempty">Connect Google Drive above to browse your files.</div>';
      updateStorageSelection();
    } else { await browseStorage(''); }
  }
  $('#stor_int_state').innerHTML = i.path ? (i.ok ? '<span class="tst done">ready</span>' : '<span class="tst failed">folder missing</span>') : '<span class="tst">not set</span>';
  $('#stor_drv_state').innerHTML = d.connected ? `<span class="tst done">connected${d.email ? ' · ' + esc(d.email) : ''}</span>` : '<span class="tst">not connected</span>';
  $('#stor_disconnect').style.display = d.connected ? '' : 'none';
  $('#stor_disconnect').disabled = storConnecting || storImporting;
  $('#stor_drive_test').disabled = !d.connected || storConnecting;
  $('#stor_connect').textContent = storConnecting ? 'Waiting for Google…' : d.connected ? 'Connect again' : 'Connect Google Drive';
  const jobs = (storState.jobs || []).slice().reverse();
  $('#stor_jobs').innerHTML = jobs.length ? jobs.map(j => {
    const pct = j.bytes ? Math.round(100 * j.sent / j.bytes) : 0;
    return `<div class="stylecard" style="margin-bottom:6px"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <b style="flex:1">${esc(j.name)}</b><span class="stchip">${j.location === 'drive' ? 'Google Drive' : 'Internal'}</span>
      <span class="tst ${j.state === 'done' ? 'done' : j.state === 'failed' ? 'failed' : 'running'}">${j.state === 'running' ? pct + '%' : esc(j.state)}</span></div>
      <div class="stnote">${j.done}/${j.files} files · ${sizeOf(j.sent)} of ${sizeOf(j.bytes)}${j.where ? ' · ' + esc(j.where) : ''}${j.error ? ' · ' + esc(j.error) : ''}</div></div>`;
  }).join('') : '<div class="navempty">Nothing copied yet. Finished episodes are copied when a switch above is on, or press "Send to storage" on an episode.</div>';
}

$('#stor_int').onsubmit = async e => {
  e.preventDefault();
  try { await api('/api/storage/internal', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: $('#stor_path').value.trim(), auto: $('#stor_int_auto').checked }) }); toast('Saved'); }
  catch (err) { toast(err.message, true); }
  loadStorage(false); if ($('#stor_loc').value === 'internal') { storTrail = []; browseStorage(''); }
};

async function saveDrive() {
  const clientId = $('#stor_cid').value.trim(), clientSecret = $('#stor_secret').value.trim();
  if (!clientId.endsWith('.apps.googleusercontent.com')) {
    $('#stor_drive_setup').open = true; $('#stor_cid').focus();
    throw new Error('Add your Google Cloud Desktop app client ID, or choose its JSON file.');
  }
  if (!clientSecret && (!storState?.drive.has_secret || clientId !== storState.drive.client_id)) {
    $('#stor_drive_setup').open = true; $('#stor_secret').focus();
    throw new Error('Add the client secret for this OAuth client, or choose its JSON file.');
  }
  await api('/api/storage/drive', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ client_id: clientId, client_secret: clientSecret || null,
                           folder: $('#stor_folder').value.trim(), auto: $('#stor_drv_auto').checked }) });
  $('#stor_secret').value = '';
  document.dispatchEvent(new Event('oneprod:drive-changed'));
}

function driveMessage(text, error = false) {
  const result = $('#stor_drive_result'); result.hidden = false;
  result.className = 'drive-result' + (error ? ' error' : ''); result.textContent = text;
}

$('#stor_client_json').onchange = async e => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    if (file.size > 64 * 1024) throw new Error('Choose the small OAuth client JSON file downloaded from Google Cloud.');
    let parsed;
    try { parsed = JSON.parse(await file.text()); }
    catch (_) { throw new Error('This file is not valid JSON. Download your OAuth client JSON from Google Cloud.'); }
    const client = parsed?.installed;
    if (!client || typeof client.client_id !== 'string' || !client.client_id.endsWith('.apps.googleusercontent.com')
        || typeof client.client_secret !== 'string' || !client.client_secret.trim()) {
      throw new Error('Choose a Desktop app OAuth client JSON file. Web app and service account keys cannot be used here.');
    }
    $('#stor_cid').value = client.client_id; $('#stor_secret').value = client.client_secret;
    driveMessage('Client details loaded. Press Connect Google Drive to sign in, or Save to finish later.');
  } catch (err) { driveMessage(err.message, true); }
  finally { e.target.value = ''; }
};

async function testDriveConnection() {
  // Reading a folder verifies the permission used by the file browser; it downloads no files.
  return await (await api('/api/storage/browse?location=drive&where=root')).json();
}
$('#stor_drive_test').onclick = async () => {
  const button = $('#stor_drive_test'); button.disabled = true;
  driveMessage('Checking access to your Drive…');
  try { await testDriveConnection(); driveMessage('Connection verified. You can browse and import files.'); }
  catch (err) { driveMessage(err.message, true); }
  finally { button.disabled = !storState?.drive.connected || storConnecting; }
};

$('#stor_drv').onsubmit = async e => {
  e.preventDefault();
  if (storConnecting) return;
  try { await saveDrive(); driveMessage('Settings saved. Press Connect Google Drive to sign in.'); }
  catch (err) { driveMessage(err.message, true); }
  await loadStorage(false);
};
$('#stor_connect').onclick = async () => {
  if (storConnecting) return;
  const attempt = ++storConnectAttempt;
  // Open during the click so popup blockers do not hide Google's consent page after the API calls.
  const tab = window.open('about:blank', '_blank');
  if (tab) tab.opener = null;
  let navigated = false;
  storConnecting = true; $('#stor_connect').disabled = true; $('#stor_drive_test').disabled = true;
  try {
    await saveDrive();
    if (attempt !== storConnectAttempt) return;
    const { url, state } = await (await api('/api/storage/drive/connect', { method: 'POST' })).json();
    if (attempt !== storConnectAttempt) return;
    $('#stor_signin_link').href = url; $('#stor_signin_fallback').hidden = false;
    if (tab && !tab.closed) { tab.location.replace(url); navigated = true; }
    driveMessage('Finish signing in with Google in the new tab. Waiting for your approval…');
    $('#stor_connect').textContent = 'Waiting for Google…';
    let connected = false;
    for (let n = 0; n < 150; n++) {
      await new Promise(r => setTimeout(r, 2000));
      if (attempt !== storConnectAttempt) return;
      const r = await (await api(`/api/storage/drive/connect/${encodeURIComponent(state)}`)).json();
      if (attempt !== storConnectAttempt) return;
      if (r.connected) { connected = true; break; }
    }
    if (!connected) throw new Error('Google sign-in timed out. Press Connect Google Drive to try again.');
    $('#stor_signin_fallback').hidden = true; $('#stor_drive_setup').open = false;
    driveMessage('Google Drive connected. Choose files below to import.');
    document.dispatchEvent(new Event('oneprod:drive-changed'));
    storTrail = []; await browseStorage('');
  } catch (err) {
    if (attempt === storConnectAttempt) { driveMessage(err.message, true); $('#stor_signin_fallback').hidden = true; }
  } finally {
    if (tab && !navigated && !tab.closed) tab.close();
    if (attempt === storConnectAttempt) {
      storConnecting = false; $('#stor_connect').disabled = false; await loadStorage(false);
    }
  }
};
$('#stor_disconnect').onclick = async () => {
  if (storConnecting || storImporting) return;
  try {
    await api('/api/storage/drive', { method: 'DELETE' }); driveMessage('Google Drive disconnected from One Production.');
    document.dispatchEvent(new Event('oneprod:drive-changed'));
  } catch (err) { driveMessage(err.message, true); }
  await loadStorage(true);
};

$('#stor_loc').onchange = () => {
  if (!storModal.classList.contains('drive-focused')) storNormalLocation = $('#stor_loc').value;
  storTrail = []; storPicked.clear(); browseStorage('');
};
$('#stor_up').onclick = () => { storTrail.pop(); storPicked.clear(); browseStorage(storTrail.length ? storTrail[storTrail.length - 1].id : ''); };

async function browseStorage(where) {
  const request = ++storBrowseRequest;
  const loc = $('#stor_loc').value, box = $('#stor_items');
  storPicked.clear(); updateStorageSelection();
  box.innerHTML = '<div class="navempty">Loading…</div>';
  $('#stor_up').disabled = !storTrail.length;
  $('#stor_where').textContent = (loc === 'drive' ? 'My Drive' : 'Storage folder') + storTrail.map(t => ' / ' + t.name).join('');
  try {
    const d = await (await api(`/api/storage/browse?location=${loc}&where=${encodeURIComponent(where)}`)).json();
    if (request !== storBrowseRequest) return;
    box.innerHTML = d.items.length ? d.items.map(it => `<label class="storrow" style="display:flex;gap:8px;align-items:center;padding:4px 6px;border-radius:8px">
        ${it.folder ? `<button type="button" class="linkbtn" data-open="${esc(it.id)}" data-name="${esc(it.name)}">📁 ${esc(it.name)}</button>`
          : `<input type="checkbox" data-pick="${esc(it.id)}" ${it.google_doc ? 'disabled title="Google Docs files can\'t be imported - download a copy first"' : ''}> <span style="flex:1">${esc(it.name)}</span><span class="stnote">${sizeOf(it.size)}</span>`}
      </label>`).join('') : '<div class="navempty">Empty folder.</div>';
    box.querySelectorAll('[data-open]').forEach(b => b.onclick = () => {
      storTrail.push({ id: b.dataset.open, name: b.dataset.name }); storPicked.clear(); browseStorage(b.dataset.open);
    });
    box.querySelectorAll('[data-pick]').forEach(c => c.onchange = () => {
      if (c.checked && storPicked.size >= 50) { c.checked = false; return toast('Import up to 50 files at a time', true); }
      c.checked ? storPicked.add(c.dataset.pick) : storPicked.delete(c.dataset.pick);
      updateStorageSelection();
    });
  } catch (e) {
    if (request !== storBrowseRequest) return;
    box.innerHTML = `<div class="navempty" role="status">${esc(e.message)}</div>`;
  }
  updateStorageSelection();
}

function updateStorageSelection() {
  $('#stor_import').disabled = !storPicked.size || !state.current || storImporting;
  $('#stor_import').textContent = storImporting ? 'Importing…' : storPicked.size ? `Import selected (${storPicked.size})` : 'Import selected';
  $('#stor_selection').textContent = !state.current ? 'Choose a project before importing files.'
    : `${storPicked.size} selected · Up to 50 files per import. Files are copied into this project; originals stay in ${$('#stor_loc').value === 'drive' ? 'Drive' : 'storage'}.`;
}

$('#stor_import').onclick = async () => {
  if (!state.current) return toast('Choose a project first', true);
  if (storImporting || !storPicked.size) return;
  const pid = state.current, loc = $('#stor_loc').value, ids = [...storPicked];
  storImporting = true; updateStorageSelection();
  $('#stor_feedback').hidden = false; $('#stor_feedback').className = 'drive-result';
  $('#stor_feedback').textContent = 'Importing selected files…';
  try {
    const r = await (await api(`/api/projects/${pid}/storage/import`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ location: loc, ids }) })).json();
    const message = `Imported ${r.imported.length} file${r.imported.length === 1 ? '' : 's'} into imports/`
      + (r.skipped.length ? ` — Skipped: ${r.skipped.join('; ')}` : '. Ready to open from this project’s Files panel.');
    $('#stor_feedback').textContent = message;
    $('#stor_feedback').classList.add(r.skipped.length ? 'error' : 'success');
    storPicked.clear(); await browseStorage(storTrail.length ? storTrail[storTrail.length - 1].id : '');
    if (state.current === pid && typeof loadFiles === 'function') loadFiles();
  } catch (e) { $('#stor_feedback').textContent = e.message; $('#stor_feedback').classList.add('error'); }
  finally { storImporting = false; updateStorageSelection(); }
};

// "Send to storage" on a finished episode (episode.js calls this)
async function sendEpisodeToStorage(pid, paths, name) {
  let st;
  try { st = await (await api('/api/storage')).json(); } catch (e) { return toast(e.message, true); }
  const locs = [];
  if (st.internal.path) locs.push('internal');
  if (st.drive.connected) locs.push('drive');
  if (!locs.length) { toast('Set up a storage first'); return openStorage(); }
  try {
    await api(`/api/projects/${pid}/storage/export`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths, name, locations: locs }) });
    toast('Copying - see Storage for progress');
    openStorage();
  } catch (e) { toast(e.message, true); }
}

$('#storbtn').onclick = openStorage;
