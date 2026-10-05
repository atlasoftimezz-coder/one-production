// Episode run: script -> voiceover -> visuals -> edit -> package, each stage behind its numeric gate.
const EPISODE_STAGE = { waiting: "waiting", running: "working", passed: "passed", needs_you: "needs you", stopped: "stopped" };
const episodeModal = document.createElement('div');
episodeModal.id = 'episodemodal';
episodeModal.className = 'scrim';
episodeModal.innerHTML = `<div class="modal wide" role="dialog" aria-label="Make an episode">
  <div class="mhead"><div style="flex:1"><h3>Make an episode</h3><p>From a finished script to an upload-ready bundle. A worker draws the visuals and writes the package, the master reviews them, and every stage has to pass its measured checks before the next one starts.</p></div><button class="iconbtn" id="episodeclose" aria-label="Close">×</button></div>
  <form id="episodeform" style="padding:20px;max-height:75vh;overflow:auto;display:grid;gap:12px">
    <label>Episode name <input id="ep_name" maxlength="80" required placeholder="e.g. Every element, part 1"></label>
    <label>Script <select id="ep_script" required></select></label>
    <label>Voiceover <select id="ep_voicejob"></select></label>
    <label id="ep_voicerow">Voice <select id="ep_voice"></select></label>
    <label>Look of the visuals (optional) <textarea id="ep_notes" maxlength="2000" placeholder="e.g. stickman explainer, white backgrounds, red labels, yellow date cards"></textarea></label>
    <div style="display:flex;gap:12px;flex-wrap:wrap"><label>Music bed (optional) <select id="ep_music"></select></label>
      <label>Size <select id="ep_size"><option>1920x1080</option><option>1280x720</option><option>3840x2160</option></select></label>
      <label style="align-self:end"><input type="checkbox" id="ep_burn"> Burn subtitles into the video</label></div>
    <div class="epapprove" style="border:1px solid var(--line);border-radius:10px;padding:10px 12px">
      <b>Heavy work this run does</b><ul id="ep_heavy" style="margin:6px 0 8px 18px"></ul>
      <label><input type="checkbox" id="ep_ok"> OK for this episode</label></div>
    <button class="btn" id="ep_go">Start the episode</button>
  </form></div>`;
document.body.append(episodeModal);
$('#episodeclose').onclick = () => episodeModal.classList.remove('open');
document.addEventListener('keydown', e => { if (e.key === 'Escape') episodeModal.classList.remove('open'); });

let episodeTools = {};
function episodeHeavy() {
  const reuse = !!$('#ep_voicejob').value;
  $('#ep_voicerow').style.display = reuse ? 'none' : '';
  const rows = [];
  if (!reuse && episodeTools.voiceover) rows.push(`<li>${esc(episodeTools.voiceover.label)}: ${esc(episodeTools.voiceover.uses)}</li>`);
  rows.push('<li>Video render: your processor (CPU) for roughly 1-3 minutes per minute of video (no graphics card)</li>');
  $('#ep_heavy').innerHTML = rows.join('');
}

$('#episodebtn').onclick = async () => {
  if (!state.current) return toast('Choose a project first', true);
  const pid = state.current;
  try {
    const [files, jobs, voice] = await Promise.all([
      (await api(`/api/projects/${pid}/files`)).json(), (await api(`/api/projects/${pid}/voicejobs`)).json(),
      (await api('/api/voice/status')).json()]);
    episodeTools = { voiceover: voice.tool };
    const names = files.map(f => f.name);
    const opt = (v, t) => `<option value="${esc(v)}">${esc(t)}</option>`;
    $('#ep_script').innerHTML = names.filter(n => /\.(md|txt|docx)$/i.test(n) && !/^(gates|episode|work|competitors)\//.test(n)).map(n => opt(n, n)).join('') || opt('', 'No script in this project yet');
    $('#ep_voicejob').innerHTML = opt('', 'Make a new voiceover') + jobs.jobs.map(j => opt(j.id, `Use ${j.name} (${j.voice})`)).join('');
    $('#ep_voice').innerHTML = voice.voices.filter(v => !v.startsWith('_upload_')).map(v => opt(v, v)).join('') || opt('', voice.running ? 'No voices' : 'Voice module not running');
    $('#ep_music').innerHTML = opt('', 'None') + names.filter(n => /\.(wav|mp3|m4a|flac|aac)$/i.test(n) && !/^(work|episode)\//.test(n)).map(n => opt(n, n)).join('');
    $('#ep_ok').checked = false;
    episodeHeavy();
    episodeModal.classList.add('open');
  } catch (e) { toast(e.message, true); }
};
$('#ep_voicejob').onchange = episodeHeavy;

$('#episodeform').onsubmit = async e => {
  e.preventDefault();
  const pid = state.current;
  const body = { name: $('#ep_name').value.trim(), script: $('#ep_script').value, voice_job: $('#ep_voicejob').value,
                 voice: $('#ep_voicejob').value ? '' : $('#ep_voice').value, visual_notes: $('#ep_notes').value.trim(),
                 music: $('#ep_music').value, size: $('#ep_size').value, burn_subtitles: $('#ep_burn').checked,
                 approve: $('#ep_ok').checked ? 'once' : null };
  if (!body.script) return toast('Add a script to the project first', true);
  if (!body.approve) return toast('Tick "OK for this episode" to allow the heavy work', true);
  if (state.busy === pid) return toast('Something is already running in this project', true);
  episodeModal.classList.remove('open');
  addMsg('user', `Episode: ${body.name}`);
  await consumeReply(() => api(`/api/projects/${pid}/episode`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                               body: JSON.stringify(body) }), null);
};

// the live panel in the chat (and the summary from history)
function renderEpisode(bubble, d, pid, prev) {
  const t = prev || { el: document.createElement('div'), act: {}, notes: [] };
  if (!prev) { t.el.className = 'teampanel episodepanel'; bubble.prepend(t.el); }
  t.data = { ...d, tasks: d.tasks || [] };
  const head = { running: 'working', paused: 'paused', reviewing: 'final report…', done: 'finished', needs_you: 'needs you', stopped: 'stopped' }[d.state] || d.state;
  const task = id => (d.tasks || []).find(x => x.id === id);
  t.el.innerHTML = `<div class="th">Episode <small>${esc((d.spec || {}).name || '')} · ${esc(head)}${d.master ? ' · master ' + esc(accShort(d.master)) : ''}</small></div>
    ${d.state === 'paused' ? `<div class="tpause"><span style="flex:1">${esc(d.paused_why || '')}</span><button class="btn" data-resume>Resume</button></div>` : ''}
    ${(d.stages || []).map(s => { const x = task(s.id);
      return `<div class="trow" data-t="${s.id}"><span class="tst ${s.status === 'passed' ? 'done' : s.status}">${EPISODE_STAGE[s.status] || s.status}</span>
        <span class="tt">${esc(s.title)}</span><span class="ta">${esc([s.detail, x && x.rounds ? 'review ' + x.rounds : '', x && x.tokens ? num(x.tokens) + ' tokens' : '', t.act[s.id] || ''].filter(Boolean).join(' · '))}</span>
        ${s.report ? `<span class="ta"><button class="linkbtn" data-report="${esc(s.report)}">gate report</button></span>` : ''}</div>`; }).join('')}
    ${(d.notes || []).slice(-3).map(n => `<div class="tnote">${esc(n)}</div>`).join('')}`;
  const r = t.el.querySelector('[data-resume]');
  if (r) r.onclick = async () => { r.disabled = true; try { await api(`/api/projects/${pid}/team/resume`, { method: 'POST' }); } catch (e) { toast(e.message, true); } };
  t.el.querySelectorAll('[data-report]').forEach(b => b.onclick = () => episodeReport(pid, b.dataset.report));
  if (d.final) {
    let f = bubble.querySelector('.seg.final');
    if (!f) { f = document.createElement('div'); f.className = 'seg final'; bubble.appendChild(f); }
    f.textContent = d.final;
  }
  return t;
}

async function episodeReport(pid, rel) {
  try {
    const r = await (await api(`/api/projects/${pid}/files/${rel.split('/').map(encodeURIComponent).join('/')}`)).json();
    toast(`${r.stage} gate: ${r.passed ? 'passed' : 'failed'} - ${r.summary}`, !r.passed);
  } catch (e) { toast(e.message, true); }
}
