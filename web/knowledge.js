// The library uses the same protected API and project selection as the main page.
let knowledgeProject = null;
let knowledgeTimer = null;
const knowledgeModal = document.createElement('div');
knowledgeModal.id = 'knowledgemodal';
knowledgeModal.className = 'scrim';
knowledgeModal.innerHTML = `<div class="modal wide" role="dialog" aria-label="Knowledge and learning">
  <div class="mhead"><div style="flex:1"><h3>Knowledge and learning</h3><p>Reference documents and directions your workers can reuse.</p></div><button class="iconbtn" id="knowledgeclose" aria-label="Close knowledge">×</button></div>
  <div style="padding:20px;max-height:75vh;overflow:auto;display:grid;gap:18px">
    <form id="knowledgecontext" style="display:flex;gap:8px;flex-wrap:wrap"><label>Channel <input id="knowledgechannel" placeholder="e.g. Finance" maxlength="100"></label><label>Resolve version <input id="knowledgeinstalled" placeholder="e.g. 21.0" maxlength="40"></label><button class="btn">Save context</button></form>
    <p>Documents stay on this PC. Relevant excerpts can be sent to the AI account you select. Importing uses no AI or GPU. Scanned pages and diagrams may need you to open the original.</p>
    <form id="knowledgeimport" style="display:grid;gap:8px"><label>Document <input id="knowledgefile" type="file" accept=".pdf,.docx,.md,.txt" required></label>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><label>Use in <select id="knowledgescope"><option value="project">This project</option><option value="channel">This channel</option><option value="global">All projects</option></select></label>
      <label>Application <input id="knowledgeapplication" placeholder="resolve" maxlength="80"></label><label>Document version <input id="knowledgeversion" placeholder="21.1" maxlength="40"></label><button class="btn" id="knowledgeimportbutton">Import document</button></div></form>
    <div id="knowledgedocuments" aria-live="polite"></div>
    <form id="knowledgesearch" style="display:flex;gap:8px"><input id="knowledgequery" aria-label="Search documents" placeholder="Search your manuals" style="flex:1" required><button class="btn">Search</button></form>
    <div id="knowledgeresults" aria-live="polite"></div>
    <div id="learningpanel"></div>
  </div></div>`;
document.body.append(knowledgeModal);

function knowledgePath() { return '/api/projects/' + encodeURIComponent(knowledgeProject) + '/knowledge'; }
function closeKnowledge() { knowledgeModal.classList.remove('open'); clearTimeout(knowledgeTimer); }
$('#knowledgeclose').onclick = closeKnowledge;
$('#knowledgebtn').onclick = async () => {
  if (!state.current) return toast('Choose a project first', true);
  knowledgeProject = state.current;
  $('#knowledgeresults').textContent = '';
  knowledgeModal.classList.add('open');
  try {
    const data = await (await api(knowledgePath())).json();
    $('#knowledgechannel').value = data.context.channel;
    $('#knowledgeinstalled').value = data.context.version;
    await refreshKnowledge();
    if (typeof refreshLearning === 'function') await refreshLearning();
  } catch (e) { toast(e.message, true); }
};

async function refreshKnowledge() {
  clearTimeout(knowledgeTimer);
  if (!knowledgeModal.classList.contains('open')) return;
  const project = knowledgeProject;
  try {
    const data = await (await api(knowledgePath())).json();
    if (project !== knowledgeProject) return;
    $('#knowledgedocuments').innerHTML = data.documents.map(d => {
      const busy = ['queued','indexing'].includes(d.status);
      return `<div style="border-top:1px solid var(--line);padding:10px 0"><strong>${esc(d.name)}</strong> <small>${esc(d.scope)} · ${esc(d.application || 'general')} ${esc(d.version)}</small><p>${esc(d.status)} · ${d.processed}/${d.total || '?'} sections · ${d.chunks} passages ${d.warnings.length ? '· ' + d.warnings.length + ' pages need inspection' : ''}</p>${d.error ? '<p>' + esc(d.error) + '</p>' : ''}<button class="btn" data-source="${d.id}">Open original</button> <button class="btn" data-document="${d.id}" data-action="${busy ? 'cancel' : 'retry'}">${busy ? 'Cancel import' : 'Rebuild index'}</button> ${busy ? '' : '<button class="btn" data-document="' + d.id + '" data-action="remove">Remove from library</button>'}${d.warnings.length ? '<details><summary>Pages needing inspection</summary>' + d.warnings.slice(0,50).map(w => '<p>Page ' + w.page + ': ' + esc(w.reason) + '</p>').join('') + '</details>' : ''}</div>`;
    }).join('') || '<p>No documents yet. Import a manual to give your workers a reference.</p>';
    $('#knowledgedocuments').querySelectorAll('[data-source]').forEach(b => b.onclick = () => openKnowledgeSource(b.dataset.source));
    $('#knowledgedocuments').querySelectorAll('[data-action]').forEach(b => b.onclick = async () => {
      try { await api(knowledgePath() + '/' + b.dataset.document + '/' + b.dataset.action, {method:'POST'}); await refreshKnowledge(); }
      catch (e) { toast(e.message, true); }
    });
    if (data.documents.some(d => ['queued','indexing'].includes(d.status))) knowledgeTimer = setTimeout(refreshKnowledge, 1500);
  } catch (e) { toast(e.message, true); }
}

$('#knowledgecontext').onsubmit = async e => {
  e.preventDefault();
  try {
    await api(knowledgePath() + '/context', {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({channel:$('#knowledgechannel').value, version:$('#knowledgeinstalled').value})});
    await refreshKnowledge();
    if (typeof refreshLearning === 'function') await refreshLearning();
    toast('Context saved');
  } catch (e) { toast(e.message, true); }
};

$('#knowledgeimport').onsubmit = async e => {
  e.preventDefault();
  const file = $('#knowledgefile').files[0];
  if (!file) return;
  const data = new FormData();
  data.append('file', file);
  for (const name of ['scope','application','version']) data.append(name, $('#knowledge' + name).value);
  const button = $('#knowledgeimportbutton');
  button.disabled = true; button.textContent = 'Uploading…';
  try { await api(knowledgePath(), {method:'POST', body:data}); $('#knowledgefile').value = ''; await refreshKnowledge(); }
  catch (e) { toast(e.message, true); }
  finally { button.disabled = false; button.textContent = 'Import document'; }
};

$('#knowledgesearch').onsubmit = async e => {
  e.preventDefault();
  try {
    const result = await (await api(knowledgePath() + '/search?q=' + encodeURIComponent($('#knowledgequery').value))).json();
    $('#knowledgeresults').innerHTML = '<p>' + esc(result.note) + '</p>' + result.matches.map(m => `<article style="border-top:1px solid var(--line);padding:12px 0"><strong>${esc(m.citation)}</strong>${m.warning ? '<p class="newerwarn">⚠ ' + esc(m.warning) + '</p>' : ''}<p>${esc(m.section)}</p><p style="white-space:pre-wrap">${esc(m.text)}</p><button class="btn" data-source="${m.document}" data-page="${m.page}">Open cited source</button></article>`).join('');
    $('#knowledgeresults').querySelectorAll('[data-source]').forEach(b => b.onclick = () => openKnowledgeSource(b.dataset.source, b.dataset.page));
  } catch (e) { toast(e.message, true); }
};

async function openKnowledgeSource(id, page=1) {
  const tab = window.open('about:blank', '_blank');
  if (tab) tab.opener = null;
  try {
    const response = await api(knowledgePath() + '/' + encodeURIComponent(id) + '/source');
    const url = URL.createObjectURL(await response.blob());
    if (tab) tab.location = url + '#page=' + Number(page);
    else { const link = document.createElement('a'); link.href = url; link.download = 'reference'; link.click(); }
    setTimeout(() => URL.revokeObjectURL(url), 120000);
  } catch (e) { if (tab) tab.close(); toast(e.message, true); }
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') closeKnowledge(); });

let correctingLesson = '';
async function refreshLearning() {
  const data = await (await api('/api/projects/' + encodeURIComponent(knowledgeProject) + '/learning')).json();
  const lessons = data.lessons.filter(l => ['active','candidate'].includes(l.state));
  $('#learningpanel').innerHTML = `<h3>Directions and learned methods</h3><p>Explicit directions are reused automatically. Worker suggestions wait here for review; verified methods include evidence. Current chat instructions take precedence.</p>
    <form id="learningform" style="display:grid;gap:8px"><label>Title <input id="learningtitle" maxlength="100" required></label><label>Direction <textarea id="learningtext" maxlength="3000" required placeholder="e.g. Use restrained transitions and keep captions clear of faces."></textarea></label><label>Remember for <select id="learningscope"><option value="project">This project</option><option value="channel">This channel</option><option value="global">All projects</option></select></label><button class="btn" id="learningsave">Remember direction</button></form>
    <form id="insightform" style="display:grid;gap:8px;border-top:1px solid var(--line);padding-top:12px;margin-top:12px"><strong>Learn from a published video</strong><p>Upload the episode's audience-retention export (YouTube Studio &gt; Analytics &gt; Advanced mode &gt; Audience retention &gt; Export &gt; CSV) to this project, pick it here. The steepest drops become suggested lessons below; nothing is used until you approve one.</p><label>Retention file <select id="insightfile"></select></label><button class="btn" id="insightgo">Find the drops</button><p id="insightnote"></p></form>
    <div id="learninglist">${lessons.map(l => `<article style="border-top:1px solid var(--line);padding:12px 0"><strong>${esc(l.title)}</strong><p>${esc(l.state)} · ${esc(l.scope)} · ${esc(l.kind)}</p><p style="white-space:pre-wrap">${esc(l.text)}</p><details><summary>Evidence</summary><pre style="white-space:pre-wrap">${esc(JSON.stringify(l.evidence,null,2))}</pre></details>${l.state === 'candidate' ? '<button class="btn" data-lesson="' + l.id + '" data-learning-action="approve">Use as my guidance</button>' : '<button class="btn" data-correct="' + l.id + '">Correct</button>'} <button class="btn" data-lesson="${l.id}" data-learning-action="forget">Forget</button></article>`).join('') || '<p>No saved directions yet.</p>'}</div>`;
  correctingLesson = '';
  $('#learningscope').value = data.default_scope;
  try {
    const csv = (await (await api('/api/projects/' + encodeURIComponent(knowledgeProject) + '/files')).json()).filter(f => f.name.toLowerCase().endsWith('.csv'));
    $('#insightfile').innerHTML = csv.map(f => `<option>${esc(f.name)}</option>`).join('') || '<option value="">No CSV in this project yet</option>';
  } catch (e) { $('#insightfile').innerHTML = '<option value="">Files unavailable</option>'; }
  $('#insightform').onsubmit = async e => {
    e.preventDefault();
    if (!$('#insightfile').value) { toast('Upload the retention CSV to this project first', true); return; }
    $('#insightgo').disabled = true; $('#insightnote').textContent = 'Reading the curve...';
    try {
      const r = await (await api('/api/projects/' + encodeURIComponent(knowledgeProject) + '/insights', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({retention:$('#insightfile').value})})).json();
      const where = r.report.drops.map(d => `${d.at} (${d.from}% → ${d.to}%${d.chapter ? ', ' + d.chapter : ''})`).join('; ');
      await refreshLearning();
      $('#insightnote').textContent = (where ? 'Drops: ' + where + '. ' + r.lessons.length + ' suggested lesson(s) below.' : '') + (r.note ? ' ' + r.note : '');
    } catch (error) { toast(error.message, true); $('#insightnote').textContent = ''; }
    finally { $('#insightgo').disabled = false; }
  };
  $('#learningform').onsubmit = async e => {
    e.preventDefault();
    try {
      await api('/api/projects/' + encodeURIComponent(knowledgeProject) + '/learning', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:$('#learningtitle').value,text:$('#learningtext').value,scope:$('#learningscope').value,supersedes:correctingLesson})});
      await refreshLearning(); toast('Direction remembered');
    } catch (error) { toast(error.message,true); }
  };
  $('#learninglist').querySelectorAll('[data-learning-action]').forEach(b => b.onclick = async () => {
    try { await api('/api/projects/' + encodeURIComponent(knowledgeProject) + '/learning/' + b.dataset.lesson + '/' + b.dataset.learningAction,{method:'POST'}); await refreshLearning(); }
    catch (e) { toast(e.message,true); }
  });
  $('#learninglist').querySelectorAll('[data-correct]').forEach(b => b.onclick = () => {
    const old = lessons.find(l => l.id === b.dataset.correct);
    correctingLesson = old.id;
    $('#learningtitle').value = old.title; $('#learningtext').value = old.text;
    $('#learningscope').value = old.scope; $('#learningscope').disabled = true;
    $('#learningsave').textContent = 'Save correction'; $('#learningtext').focus();
  });
}
