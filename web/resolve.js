// Resolve plans: workers propose, the owner reviews and runs each one on a NEW Resolve project.
let resolveProject = null;
const resolveModal = document.createElement('div');
resolveModal.id = 'resolvemodal';
resolveModal.className = 'scrim';
resolveModal.innerHTML = `<div class="modal wide" role="dialog" aria-label="DaVinci Resolve plans">
  <div class="mhead"><div style="flex:1"><h3>DaVinci Resolve plans</h3><p>Workers can only propose edits. You review each plan and run it; it always builds a new project and never touches your existing ones. No rendering.</p></div><button class="iconbtn" id="resolveclose" aria-label="Close Resolve plans">×</button></div>
  <div style="padding:20px;max-height:75vh;overflow:auto;display:grid;gap:18px">
    <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap"><label><input type="checkbox" id="resolveenabled"> Let One Production control DaVinci Resolve Studio</label><button class="btn" id="resolvecheck">Check Resolve</button></div>
    <p id="resolvestatus" aria-live="polite">Needs the paid Resolve Studio running, with Preferences &gt; System &gt; General &gt; External scripting using set to Local.</p>
    <div id="resolveplans" aria-live="polite"></div>
    <details><summary>Add a plan yourself (JSON)</summary><form id="resolveadd" style="display:grid;gap:8px;margin-top:8px"><textarea id="resolveplanjson" rows="8" required placeholder='{"title":"Rough cut","operations":[{"op":"import_media","files":["vo.wav"]},{"op":"create_timeline","name":"Main"},{"op":"append_clips","files":["vo.wav"]}]}'></textarea><button class="btn">Check and add plan</button></form></details>
  </div></div>`;
document.body.append(resolveModal);

function resolvePath() { return '/api/projects/' + encodeURIComponent(resolveProject) + '/resolve/plans'; }
function closeResolve() { resolveModal.classList.remove('open'); }
$('#resolveclose').onclick = closeResolve;
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeResolve(); });

$('#resolvebtn').onclick = async () => {
  if (!state.current) return toast('Choose a project first', true);
  resolveProject = state.current;
  try {
    // read the switch before showing it, so a quick click can't be overwritten by a late answer
    const data = await (await api('/api/resolve')).json();
    $('#resolveenabled').checked = data.enabled;
    resolveModal.classList.add('open');
    await refreshResolve();
  } catch (e) { toast(e.message, true); }
};

$('#resolveenabled').onchange = async () => {
  try {
    await api('/api/resolve', {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled:$('#resolveenabled').checked})});
    toast($('#resolveenabled').checked ? 'Resolve control on' : 'Resolve control off');
  } catch (e) { toast(e.message, true); }
};

$('#resolvecheck').onclick = async () => {
  $('#resolvestatus').textContent = 'Asking Resolve…';
  try {
    const s = (await (await api('/api/resolve?probe=true')).json()).status;
    $('#resolvestatus').textContent = s.available
      ? `${s.product} ${s.version} is running${s.current_project ? ' · open project: ' + s.current_project : ''}.`
      : s.reason;
  } catch (e) { $('#resolvestatus').textContent = e.message; }
};

function resolveStep(op) {
  if (op.op === 'import_media') return 'Import ' + op.files.join(', ');
  if (op.op === 'create_timeline') return 'New timeline "' + op.name + '"';
  if (op.op === 'append_clips') return 'Append ' + op.files.join(', ');
  return `Marker "${op.name}" (${op.color}) at frame ${op.frame}${op.note ? ': ' + op.note : ''}`;
}

function resolveRun(run) {
  if (!run) return '';
  const head = run.passed ? `Passed · created "${run.project}" in Resolve ${run.version}` : `Failed: ${run.error}${run.changed ? ` · partial project "${run.project}" left for you to inspect or delete` : ' · nothing was changed in Resolve'}`;
  return `<div class="resolverun"><p><strong>${esc(head)}</strong></p>${run.previous_project ? '<p>Your project "' + esc(run.previous_project) + '" was left as it was; switch back to it in Resolve.</p>' : ''}<details><summary>Checks read back from Resolve</summary><pre style="white-space:pre-wrap">${esc(JSON.stringify(run.checks, null, 2))}</pre></details>${run.lesson ? '<p>Saved as a verified method in Knowledge and learning.</p>' : ''}</div>`;
}

async function refreshResolve() {
  const project = resolveProject;
  const data = await (await api(resolvePath())).json();
  if (project !== resolveProject) return;
  $('#resolveplans').innerHTML = data.plans.map(p => {
    const runnable = ['proposed', 'failed'].includes(p.status);
    const s = p.plan.settings;
    return `<article class="resolveplan" style="border-top:1px solid var(--line);padding:12px 0" data-plan="${p.id}">
      <strong>${esc(p.plan.title)}</strong> <small>${esc(p.status)} · proposed by ${p.source === 'worker' ? 'a worker' : 'you'} · checked against Resolve ${esc(p.version_checked || 'version unknown')}</small>
      ${p.warnings.map(w => '<p>⚠ ' + esc(w) + '</p>').join('')}
      ${Object.keys(s).length ? '<p>Settings: ' + esc(Object.entries(s).map(([k, v]) => k + ' ' + v).join(', ')) + '</p>' : ''}
      <ol>${p.plan.operations.map(op => '<li>' + esc(resolveStep(op)) + '</li>').join('')}</ol>
      ${p.plan.citations.length ? '<p>Sources: ' + esc(p.plan.citations.map(c => c.citation || c.document).join('; ')) + '</p>' : ''}
      ${resolveRun(p.last_run)}
      ${runnable ? `<label style="display:block;margin:8px 0"><input type="checkbox" data-saved> I saved my open Resolve project. Resolve will switch to the new project.</label><button class="btn" data-run="${p.id}" data-hash="${p.hash}">Run on a new Resolve project</button> ` : ''}<button class="btn" data-discard="${p.id}">Discard</button>
    </article>`;
  }).join('') || '<p>No plans yet. Ask in the chat for a Resolve edit; the worker\'s plan appears here for you to review.</p>';
  $('#resolveplans').querySelectorAll('[data-run]').forEach(b => b.onclick = async () => {
    const saved = b.closest('article').querySelector('[data-saved]').checked;
    if (!saved) return toast('Save your open Resolve project first, then tick the box', true);
    b.disabled = true; b.textContent = 'Running in Resolve…';
    try {
      const run = await (await api(resolvePath() + '/' + b.dataset.run + '/execute', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({plan_hash:b.dataset.hash, confirm_saved:true})})).json();
      toast(run.passed ? 'Plan ran and was verified' : 'Plan failed: ' + run.error, !run.passed);
    } catch (e) { toast(e.message, true); }
    await refreshResolve();
  });
  $('#resolveplans').querySelectorAll('[data-discard]').forEach(b => b.onclick = async () => {
    try { await api(resolvePath() + '/' + b.dataset.discard + '/discard', {method:'POST'}); await refreshResolve(); }
    catch (e) { toast(e.message, true); }
  });
}

$('#resolveadd').onsubmit = async e => {
  e.preventDefault();
  let plan;
  try { plan = JSON.parse($('#resolveplanjson').value); } catch { return toast('That is not valid JSON', true); }
  try {
    await api(resolvePath(), {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(plan)});
    $('#resolveplanjson').value = '';
    await refreshResolve(); toast('Plan checked and added');
  } catch (err) { toast(err.message, true); }
};
