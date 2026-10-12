// Storage page (owner, 2026-10-12): finished episodes copied to an internal folder and/or Google Drive (the
// owner's Drive API client, their 5 TB plan), and files imported from either into the current project.
const storModal = document.createElement('div');
storModal.id = 'stormodal';
storModal.className = 'scrim';
storModal.innerHTML = `<div class="modal wide" role="dialog" aria-label="Storage">
  <div class="mhead"><div style="flex:1"><h3>Storage</h3><p>Where finished episodes are copied, and where you can pull files into a project from. Projects keep working on this PC (fast for Resolve); storage gets a copy.</p></div><button class="iconbtn" id="storclose" aria-label="Close Storage">×</button></div>
  <div style="padding:20px;max-height:78vh;overflow:auto;display:grid;gap:16px">
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px">
      <form class="stylecard" id="stor_int" style="display:grid;gap:8px;margin:0">
        <div style="display:flex;gap:8px;align-items:center"><b style="flex:1">Internal storage</b><span id="stor_int_state"></span></div>
        <label>Folder <input id="stor_path" placeholder="E:\\One Production exports"></label>
        <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="stor_int_auto"> Copy every finished episode here</label>
        <div><button class="btn primary">Save</button></div>
        <p class="stnote">Any folder this PC can write to: another drive, an external disk or a network share.</p>
      </form>
      <form class="stylecard" id="stor_drv" style="display:grid;gap:8px;margin:0">
        <div style="display:flex;gap:8px;align-items:center"><b style="flex:1">Google Drive</b><span id="stor_drv_state"></span></div>
        <label>OAuth client ID <input id="stor_cid" placeholder="....apps.googleusercontent.com"></label>
        <label>Client secret <input id="stor_secret" type="password" autocomplete="off" placeholder="kept in Credential Manager - leave empty to keep"></label>
        <label>Folder in your Drive <input id="stor_folder" value="One Production" maxlength="120"></label>
        <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="stor_drv_auto"> Copy every finished episode to Drive</label>
        <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn">Save</button>
          <button type="button" class="btn primary" id="stor_connect">Connect Google Drive</button>
          <button type="button" class="btn" id="stor_disconnect" style="display:none">Disconnect</button></div>
        <p class="stnote">Your own OAuth client (Google Cloud console → APIs &amp; Services → Credentials → OAuth client ID → Desktop app), with the Google Drive API enabled. One Production asks for: create and upload its own files, and read your Drive to import.</p>
      </form>
    </div>
    <div class="stylecard" style="margin:0">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b style="flex:1">Import into this project</b>
        <select id="stor_loc"><option value="internal">Internal storage</option><option value="drive">Google Drive</option></select>
        <button class="btn" id="stor_up" title="Up one folder">↑ Up</button>
        <button class="btn primary" id="stor_import" disabled>Import selected</button></div>
      <div class="stnote" id="stor_where" style="margin:6px 0"></div>
      <div id="stor_items" style="display:grid;gap:2px;max-height:300px;overflow:auto"></div>
    </div>
    <div><div class="sect" style="padding:0 0 6px">Copies</div><div id="stor_jobs" aria-live="polite"></div></div>
  </div></div>`;
document.body.append(storModal);
$('#storclose').onclick = () => { storModal.classList.remove('open'); clearInterval(storTimer); };
document.addEventListener('keydown', e => { if (e.key === 'Escape') { storModal.classList.remove('open'); clearInterval(storTimer); } });

let storState = null, storTimer = null, storTrail = [];
const storPicked = new Set();
const sizeOf = b => b == null ? '' : b > 1e9 ? (b / 1e9).toFixed(1) + ' GB' : b > 1e6 ? (b / 1e6).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1e3)) + ' KB';

async function openStorage() {
  storModal.classList.add('open');
  await loadStorage(true);
  clearInterval(storTimer);
  storTimer = setInterval(() => { if (storModal.classList.contains('open')) loadStorage(false); }, 3000);
}

async function loadStorage(fill) {
  try { storState = await (await api('/api/storage')).json(); } catch (e) { return toast(e.message, true); }
  const i = storState.internal, d = storState.drive;
  if (fill) {
    $('#stor_path').value = i.path || ''; $('#stor_int_auto').checked = !!i.auto;
    $('#stor_cid').value = d.client_id || ''; $('#stor_folder').value = d.folder || 'One Production';
    $('#stor_drv_auto').checked = !!d.auto; $('#stor_secret').value = '';
    storTrail = []; storPicked.clear(); browseStorage('');
  }
  $('#stor_int_state').innerHTML = i.path ? (i.ok ? '<span class="tst done">ready</span>' : '<span class="tst failed">folder missing</span>') : '<span class="tst">not set</span>';
  $('#stor_drv_state').innerHTML = d.connected ? `<span class="tst done">connected${d.email ? ' · ' + esc(d.email) : ''}</span>` : '<span class="tst">not connected</span>';
  $('#stor_disconnect').style.display = d.connected ? '' : 'none';
  $('#stor_connect').textContent = d.connected ? 'Connect again' : 'Connect Google Drive';
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
  await api('/api/storage/drive', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ client_id: $('#stor_cid').value.trim(), client_secret: $('#stor_secret').value || null,
                           folder: $('#stor_folder').value.trim(), auto: $('#stor_drv_auto').checked }) });
  $('#stor_secret').value = '';
}
$('#stor_drv').onsubmit = async e => {
  e.preventDefault();
  try { await saveDrive(); toast('Saved'); } catch (err) { toast(err.message, true); }
  loadStorage(false);
};
$('#stor_connect').onclick = async () => {
  try {
    await saveDrive();
    const { url, state } = await (await api('/api/storage/drive/connect', { method: 'POST' })).json();
    window.open(url, '_blank', 'noopener');
    toast('Finish the sign-in in the Google tab');
    for (let n = 0; n < 150; n++) {
      await new Promise(r => setTimeout(r, 2000));
      const r = await (await api(`/api/storage/drive/connect/${encodeURIComponent(state)}`)).json();
      if (r.connected) { toast('Google Drive connected'); break; }
    }
  } catch (err) { toast(err.message, true); }
  loadStorage(true);
};
$('#stor_disconnect').onclick = async () => {
  try { await api('/api/storage/drive', { method: 'DELETE' }); toast('Disconnected'); } catch (err) { toast(err.message, true); }
  loadStorage(true);
};

$('#stor_loc').onchange = () => { storTrail = []; storPicked.clear(); browseStorage(''); };
$('#stor_up').onclick = () => { storTrail.pop(); storPicked.clear(); browseStorage(storTrail.length ? storTrail[storTrail.length - 1].id : ''); };

async function browseStorage(where) {
  const loc = $('#stor_loc').value, box = $('#stor_items');
  box.innerHTML = '<div class="navempty">Loading…</div>';
  $('#stor_where').textContent = (loc === 'drive' ? 'My Drive' : 'Storage folder') + storTrail.map(t => ' / ' + t.name).join('');
  try {
    const d = await (await api(`/api/storage/browse?location=${loc}&where=${encodeURIComponent(where)}`)).json();
    box.innerHTML = d.items.length ? d.items.map(it => `<label class="storrow" style="display:flex;gap:8px;align-items:center;padding:4px 6px;border-radius:8px">
        ${it.folder ? `<button type="button" class="linkbtn" data-open="${esc(it.id)}" data-name="${esc(it.name)}">📁 ${esc(it.name)}</button>`
          : `<input type="checkbox" data-pick="${esc(it.id)}" ${it.google_doc ? 'disabled title="Google Docs files can\'t be imported - download a copy first"' : ''}> <span style="flex:1">${esc(it.name)}</span><span class="stnote">${sizeOf(it.size)}</span>`}
      </label>`).join('') : '<div class="navempty">Empty folder.</div>';
    box.querySelectorAll('[data-open]').forEach(b => b.onclick = () => {
      storTrail.push({ id: b.dataset.open, name: b.dataset.name }); storPicked.clear(); browseStorage(b.dataset.open);
    });
    box.querySelectorAll('[data-pick]').forEach(c => c.onchange = () => {
      c.checked ? storPicked.add(c.dataset.pick) : storPicked.delete(c.dataset.pick);
      $('#stor_import').disabled = !storPicked.size;
    });
  } catch (e) { box.innerHTML = `<div class="navempty">${esc(e.message)}</div>`; }
  $('#stor_import').disabled = !storPicked.size;
}

$('#stor_import').onclick = async () => {
  if (!state.current) return toast('Choose a project first', true);
  $('#stor_import').disabled = true;
  try {
    const r = await (await api(`/api/projects/${state.current}/storage/import`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ location: $('#stor_loc').value, ids: [...storPicked] }) })).json();
    toast(`Imported ${r.imported.length} file${r.imported.length === 1 ? '' : 's'} into imports/` + (r.skipped.length ? ` - skipped: ${r.skipped.join('; ')}` : ''), !!r.skipped.length);
    storPicked.clear(); browseStorage(storTrail.length ? storTrail[storTrail.length - 1].id : '');
    if (typeof loadFiles === 'function') loadFiles();
  } catch (e) { toast(e.message, true); }
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
