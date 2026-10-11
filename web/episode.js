// Episode run: script -> voiceover -> Fusion visuals -> your render -> package, each stage behind its check.
const EPISODE_STAGE = { waiting: "waiting", running: "working", passed: "passed", passed_with_notes: "check", needs_you: "needs you", stopped: "stopped" };
const EPISODE_HEAD = { running: 'working', paused: 'paused', reviewing: 'final report…', done: 'finished', needs_you: 'needs you',
                       stopped: 'stopped', waiting_render: 'waiting for your render' };
const episodeModal = document.createElement('div');
episodeModal.id = 'episodemodal';
episodeModal.className = 'scrim';
episodeModal.innerHTML = `<div class="modal wide" role="dialog" aria-label="Make an episode">
  <div class="mhead"><div style="flex:1"><h3>Make an episode</h3><p>From a finished script to a video. As a draft (the default) it runs start to finish on its own: voiceover, a new look and animation for this script in the competitor style you pick, Fusion shots in DaVinci Resolve (1080p, 60 fps), footage, sound effects, Resolve's render and the package - then a review report lists what to check. Without the draft switch you approve every shot script and render yourself.</p></div><button class="iconbtn" id="episodeclose" aria-label="Close">×</button></div>
  <form id="episodeform" style="padding:20px;max-height:75vh;overflow:auto;display:grid;gap:12px">
    <label>Episode name <input id="ep_name" maxlength="80" required placeholder="e.g. Every element, part 1"></label>
    <label>Script <select id="ep_script" required></select></label>
    <label>Voiceover <select id="ep_voicejob"></select></label>
    <label id="ep_voicerow">Voice <select id="ep_voice"></select></label>
    <label class="epdraft"><input type="checkbox" id="ep_draft" checked> <b>Make a draft with no stops</b> - the whole video in one go for your first review: failed checks become notes instead of "needs you", and Resolve renders the draft</label>
    <div><b>Edit in the style of</b> <span class="epnote">(learned in the Competitors tab)</span><div id="ep_styles" class="epstyles"></div></div>
    <label>Look of the visuals (optional) <textarea id="ep_notes" maxlength="2000" placeholder="Optional - e.g. calm documentary feel, lots of maps. Leave empty and the AI designs a look for this script."></textarea></label>
    <div style="display:flex;gap:12px;flex-wrap:wrap"><label>Music bed (optional) <select id="ep_music"></select></label>
</div>
    <div class="epapprove" style="border:1px solid var(--line);border-radius:10px;padding:10px 12px">
      <b>Heavy work this run does</b><ul id="ep_heavy" style="margin:6px 0 8px 18px"></ul>
      <label><input type="checkbox" id="ep_ok"> OK for this episode</label></div>
    <button class="btn" id="ep_go">Start the episode</button>
  </form></div>`;
document.body.append(episodeModal);
$('#episodeclose').onclick = () => episodeModal.classList.remove('open');
document.addEventListener('keydown', e => { if (e.key === 'Escape') episodeModal.classList.remove('open'); });

let episodeTools = {}, episodePre = null;
function episodeHeavy() {
  const reuse = !!$('#ep_voicejob').value, draft = $('#ep_draft').checked;
  $('#ep_voicerow').style.display = reuse ? 'none' : '';
  const rows = [];
  if (!reuse && episodeTools.voiceover) rows.push(`<li>${esc(episodeTools.voiceover.label)}: ${esc(episodeTools.voiceover.uses)}</li>`);
  if (draft) {
    rows.push('<li>Visuals: built in DaVinci Resolve Studio. Shot scripts for this episode\'s own shots run <b>without a card</b> (only inside its own new Resolve project); anything else still asks</li>');
    rows.push('<li>Render: Resolve renders the draft (1080p 60 fps) into this project - Resolve is busy for a few minutes</li>');
    const ok = id => episodePre && (episodePre.checks.find(c => c.id === id) || {}).ok;
    if (ok('pexels')) rows.push('<li>Pexels footage where it explains better (your Pexels key)</li>');
    if (ok('sfx')) rows.push('<li>Sound effects from Epidemic Sound - each new effect is one download on your plan</li>');
    rows.push('<li>AI use: about one Claude turn per 10 shots plus planning, checking and the review report</li>');
  } else {
    rows.push('<li>Visuals: built in DaVinci Resolve Studio (Resolve control and the Resolve MCP switched on, Resolve open); you approve each shot script and render the timeline yourself</li>');
  }
  if (episodePre) rows.push(...episodePre.checks.map(c => `<li class="${c.ok ? 'ok' : (c.optional ? 'opt' : 'bad')}">${c.ok ? '✓' : (c.optional ? '–' : '✗')} ${esc(c.text)}</li>`));
  $('#ep_heavy').innerHTML = rows.join('');
}

async function episodeStyles() {
  try {
    const items = (await (await api('/api/styles')).json()).filter(s => s.available);
    $('#ep_styles').innerHTML = items.length
      ? items.map(s => `<label><input type="checkbox" value="${esc(s.id)}" ${s.lesson ? 'checked' : ''}> ${esc(s.name)}
          <span class="epnote">${s.measured && s.measured.pace ? esc(`${s.measured.pace.cuts_per_min} cuts/min, shot ${s.measured.pace.shot_median} s`) : ''}</span></label>`).join('')
      : '<span class="epnote">No styles yet - paste competitor links in the Competitors tab first (optional).</span>';
  } catch (e) { $('#ep_styles').textContent = e.message; }
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
    episodePre = null;
    episodeHeavy();
    episodeModal.classList.add('open');
    await episodeStyles();
    try { episodePre = await (await api(`/api/projects/${pid}/episode/preflight`)).json(); } catch (e) { episodePre = null; }
    episodeHeavy();
  } catch (e) { toast(e.message, true); }
};
$('#ep_voicejob').onchange = episodeHeavy;
$('#ep_draft').onchange = episodeHeavy;

$('#episodeform').onsubmit = async e => {
  e.preventDefault();
  const pid = state.current;
  const body = { name: $('#ep_name').value.trim(), script: $('#ep_script').value, voice_job: $('#ep_voicejob').value,
                 voice: $('#ep_voicejob').value ? '' : $('#ep_voice').value, visual_notes: $('#ep_notes').value.trim(),
                 music: $('#ep_music').value, size: '1920x1080', burn_subtitles: false,
                 approve: $('#ep_ok').checked ? 'once' : null, draft: $('#ep_draft').checked,
                 styles: [...document.querySelectorAll('#ep_styles input:checked')].map(i => i.value) };
  if (!body.script) return toast('Add a script to the project first', true);
  if (!body.approve) return toast('Tick "OK for this episode" to allow the heavy work', true);
  if (body.draft && episodePre && !episodePre.ready)
    return toast('The draft can\'t run yet: ' + episodePre.checks.filter(c => !c.ok && !c.optional).map(c => c.text).join('; '), true);
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
  const head = EPISODE_HEAD[d.state] || d.state;
  const task = id => (d.tasks || []).find(x => x.id === id);
  t.el.innerHTML = `<div class="th">${d.draft ? 'Draft episode' : 'Episode'} <small>${esc((d.spec || {}).name || '')} · ${esc(head)}${d.master ? ' · master ' + esc(accShort(d.master)) : ''}</small></div>
    ${d.state === 'paused' ? `<div class="tpause"><span style="flex:1">${esc(d.paused_why || '')}</span><button class="btn" data-resume>Resume</button></div>` : ''}
    ${(d.stages || []).map(s => { const x = task(s.id);
      return `<div class="trow" data-t="${s.id}"><span class="tst ${s.status === 'passed' ? 'done' : s.status === 'passed_with_notes' ? 'paused' : s.status}">${EPISODE_STAGE[s.status] || s.status}</span>
        <span class="tt">${esc(s.title)}</span><span class="ta">${esc([s.detail, x && x.rounds ? 'review ' + x.rounds : '', x && x.tokens ? num(x.tokens) + ' tokens' : '', t.act[s.id] || ''].filter(Boolean).join(' · '))}</span>
        ${s.report ? `<span class="ta"><button class="linkbtn" data-report="${esc(s.report)}">gate report</button></span>` : ''}</div>`; }).join('')}
    ${(d.notes || []).slice(-3).map(n => `<div class="tnote">${esc(n)}</div>`).join('')}
    ${(d.flags || []).length ? `<div class="epflags"><b>To check in your review (${d.flags.length})</b>${d.flags.map(f =>
        `<div class="tnote">${esc(f.stage)}: ${esc(f.why)}</div>`).join('')}</div>` : ''}
    ${d.draft && d.state === 'done' && (d.vo || {}).video ? `<div class="tnote">Draft video: <b>${esc(d.vo.video)}</b> · Resolve project: ${esc(d.vo.resolve_project || '')}</div>` : ''}
    ${d.state === 'waiting_render' ? `<div class="tpause eprender" style="flex-wrap:wrap;gap:8px"><span style="flex:1 1 100%">Render the "Episode" timeline of Resolve project
      "${esc((d.result || {}).project || '')}" (Deliver page: H.264, 1920x1080, 60 fps) into this project's folder, then pick it:</span>
      <select data-video style="flex:1"><option value="">Loading videos…</option></select><button class="btn" data-rendered>Check and package</button></div>` : ''}`;
  const r = t.el.querySelector('[data-resume]');
  if (r) r.onclick = async () => { r.disabled = true; try { await api(`/api/projects/${pid}/team/resume`, { method: 'POST' }); } catch (e) { toast(e.message, true); } };
  t.el.querySelectorAll('[data-report]').forEach(b => b.onclick = () => episodeReport(pid, b.dataset.report));
  const pick = t.el.querySelector('[data-video]');
  if (pick) episodeVideos(pid, pick, t.el.querySelector('[data-rendered]'));
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

async function episodeVideos(pid, select, button) {
  try {
    const files = await (await api(`/api/projects/${pid}/files`)).json();
    const vids = files.map(f => f.name).filter(n => /\.(mp4|mov|mkv)$/i.test(n) && !/^(episode|stock)\//.test(n));
    select.innerHTML = vids.length ? vids.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('')
                                   : '<option value="">No video in this project yet - render or upload it, then reopen</option>';
  } catch (e) { toast(e.message, true); }
  button.onclick = async () => {
    if (!select.value) return toast('Render the timeline into this project first', true);
    if (state.busy === pid) return toast('Something is already running in this project', true);
    button.disabled = true;
    addMsg('user', `Rendered video: ${select.value}`);
    await consumeReply(() => api(`/api/projects/${pid}/episode/render`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                                         body: JSON.stringify({ video: select.value }) }), null);
  };
}
