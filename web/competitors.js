// Competitors: paste competitor links, the app watches the videos (cuts, frames, sound) and the AI learns
// their editing style into a profile that episodes are edited with. Owner, 2026-10-11.
const STYLE_STAGE = { waiting: 'waiting', running: 'working', passed: 'done', needs_you: 'needs you', stopped: 'stopped' };
const compModal = document.createElement('div');
compModal.id = 'compmodal';
compModal.className = 'scrim';
compModal.innerHTML = `<div class="modal wide" role="dialog" aria-label="Competitors">
  <div class="mhead"><div style="flex:1"><h3>Competitors</h3><p>Paste competitor videos or channels. One Production watches each video - every cut, the frames, the sound and the words - and the AI learns its editing style. Episodes are then edited in that style, with new animation for every script (never copied).</p></div><button class="iconbtn" id="compclose" aria-label="Close Competitors">×</button></div>
  <div style="padding:20px;max-height:75vh;overflow:auto;display:grid;gap:16px">
    <form id="compform" style="display:grid;gap:8px">
      <label>Links <textarea id="comp_links" rows="3" required placeholder="https://www.youtube.com/watch?v=...  or a channel: https://www.youtube.com/@name"></textarea></label>
      <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:end">
        <label style="flex:1;min-width:200px">Name of this style <input id="comp_name" maxlength="60" placeholder="e.g. Finance explainer, fast"></label>
        <label>Videos per channel <input id="comp_per" type="number" min="1" max="10" value="3" style="width:70px"></label>
        <button class="btn primary" id="comp_go">Watch and learn</button></div>
      <p class="stnote">Downloads a small copy (up to 720p, 20 min) of each video for measuring only - it is never used in your videos and is deleted afterwards. Measuring is free (processor). Learning uses one Claude turn that looks at the frames.</p>
    </form>
    <div><div class="sect" style="padding:0 0 6px">Learned styles</div><div id="comp_list" aria-live="polite"></div></div>
  </div></div>`;
document.body.append(compModal);
$('#compclose').onclick = () => compModal.classList.remove('open');
document.addEventListener('keydown', e => { if (e.key === 'Escape') compModal.classList.remove('open'); });

$('#compbtn').onclick = async () => {
  compModal.classList.add('open');
  await loadStyles();
};

$('#compform').onsubmit = async e => {
  e.preventDefault();
  if (!state.current) return toast('Choose a project first - the frames are saved in it', true);
  const pid = state.current;
  if (state.busy === pid) return toast('Something is already running in this project', true);
  const body = { links_text: $('#comp_links').value, name: $('#comp_name').value.trim(),
                 per_channel: Number($('#comp_per').value) || 3 };
  compModal.classList.remove('open');
  addMsg('user', `Learn the editing style${body.name ? ' "' + body.name + '"' : ''}`);
  await consumeReply(() => api(`/api/projects/${pid}/style`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                              body: JSON.stringify(body) }), null);
  $('#comp_links').value = '';
};

function styleNumbers(m) {
  const p = (m && m.pace) || {}, s = (m && m.sound) || {}, w = (m && m.words) || {};
  const pct = v => v == null ? '?' : Math.round(v * 100) + '%';
  return [p.beats_per_min != null ? `${p.beats_per_min} changes/min` : '', p.beat_median != null ? `every ${p.beat_median} s (median)` : '',
          p.first_30s_beats_per_min != null ? `hook ${p.first_30s_beats_per_min}/min` : '',
          p.cuts_per_min != null ? `${p.cuts_per_min} hard cuts/min` : '', (p.continuous_share || 0) >= 0.5 ? 'continuous canvas' : '',
          s.cuts_with_a_hit != null ? `SFX on ${pct(s.cuts_with_a_hit)} of cuts` : '',
          s.music_bed_hint_share != null ? `music bed ${pct(s.music_bed_hint_share)}` : '', w.wpm ? `${w.wpm} wpm` : '']
    .filter(Boolean).map(x => `<span class="stchip">${esc(x)}</span>`).join(' ');
}

async function loadStyles() {
  const box = $('#comp_list');
  try {
    const items = await (await api('/api/styles')).json();
    box.innerHTML = items.length ? items.map(e => `<div class="stylecard" data-id="${esc(e.id)}">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b style="flex:1">${esc(e.name)}</b>
          ${e.lesson ? `<span class="tst done">guidance (${esc(e.scope || 'project')})</span>` : ''}
          ${e.available ? '' : '<span class="tst failed">files missing</span>'}</div>
        <div class="meta">${esc(e.videos.map(v => v.title || v.id).join(' · ').slice(0, 200))}</div>
        <div style="margin:6px 0">${styleNumbers(e.measured)}</div>
        <div class="meta" style="white-space:normal">${esc(e.summary || '')}</div>
        <div class="stacts">
          <button class="btn" data-a="view">See what it learned</button>
          <select data-scope><option value="project">this project</option><option value="channel">this channel</option><option value="global">all projects</option></select>
          <button class="btn primary" data-a="approve">${e.lesson ? 'Update guidance' : 'Use as guidance'}</button>
          <button class="btn" data-a="rename">Rename</button>
          <button class="btn danger" data-a="del">Delete</button></div>
        <div class="styledetail"></div></div>`).join('')
      : '<div class="navempty">No styles yet. Paste a competitor link above.</div>';
    box.querySelectorAll('.stylecard').forEach(card => {
      const id = card.dataset.id, e = items.find(x => x.id === id);
      card.querySelectorAll('button[data-a]').forEach(b => b.onclick = () => styleAction(card, e, b.dataset.a, b));
    });
  } catch (err) { box.innerHTML = `<div class="navempty">${esc(err.message)}</div>`; }
}

async function styleAction(card, e, act, btn) {
  try {
    if (act === 'view') {
      const d = card.querySelector('.styledetail');
      if (d.innerHTML) { d.innerHTML = ''; return; }
      const full = await (await api(`/api/styles/${e.id}`)).json();
      d.innerHTML = styleDetail(full.profile);
      for (const v of (full.profile.videos || [])) {
        const rel = `${full.path}/${v.id}/sheets/sheet-01.jpg`;
        try {
          const blob = await (await api(`/api/projects/${full.project}/files/${rel.split('/').map(encodeURIComponent).join('/')}`)).blob();
          d.querySelector('.sheets').insertAdjacentHTML('beforeend', `<img alt="Frames of ${esc(v.title || v.id)}" src="${URL.createObjectURL(blob)}">`);
        } catch (_) { /* a missing sheet is fine */ }
      }
    } else if (act === 'approve') {
      const scope = card.querySelector('[data-scope]').value;
      await api(`/api/styles/${e.id}/approve`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope }) });
      toast(`"${e.name}" is now guidance for ${scope === 'global' ? 'all projects' : 'this ' + scope}`);
      await loadStyles();
    } else if (act === 'rename') {
      const ask = card.querySelector('.renask');
      if (ask) return;
      card.querySelector('b').insertAdjacentHTML('afterend', `<span class="renask" style="display:flex;gap:6px"><input value="${esc(e.name)}" maxlength="60"><button class="btn primary">Save</button></span>`);
      const box = card.querySelector('.renask');
      box.querySelector('button').onclick = async () => {
        await api(`/api/styles/${e.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: box.querySelector('input').value }) });
        await loadStyles();
      };
    } else if (act === 'del') {
      if (btn.dataset.sure !== '1') { btn.dataset.sure = '1'; btn.textContent = 'Click again to delete'; return; }
      await api(`/api/styles/${e.id}`, { method: 'DELETE' });
      toast(`Deleted "${e.name}"`);
      await loadStyles();
    }
  } catch (err) { toast(err.message, true); }
}

function styleDetail(p) {
  const obj = o => typeof o === 'string' ? esc(o) : Object.entries(o || {}).map(([k, v]) => `<b>${esc(k.replace(/_/g, ' '))}:</b> ${esc(typeof v === 'string' ? v : JSON.stringify(v))}`).join('<br>');
  const kinds = Array.isArray(p.visual_language) ? p.visual_language.map(k => `<li><b>${esc(k.kind || '')}</b> ${k.share != null ? esc(String(k.share)) + '%' : ''} - ${esc(k.how || '')}</li>`).join('') : `<li>${obj(p.visual_language)}</li>`;
  return `<div class="styleprof">
    <div class="sheets"></div>
    <p><b>Pacing</b><br>${obj(p.pacing)}</p>
    <p><b>Kinds of shots</b></p><ul>${kinds}</ul>
    <p><b>Text</b><br>${obj(p.typography)}</p><p><b>Colour</b><br>${obj(p.color)}</p>
    <p><b>Motion</b><br>${obj(p.motion)}</p><p><b>Sound</b><br>${obj(p.sound)}</p>
    <p><b>Do</b></p><ul>${(p.do || []).map(x => `<li>${esc(x)}</li>`).join('')}</ul>
    <p><b>Don't</b></p><ul>${(p.dont || []).map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>`;
}

// the live panel in the chat (and the summary from history)
function renderStyle(bubble, d, pid, prev) {
  const t = prev || { el: document.createElement('div'), act: {}, notes: [] };
  if (!prev) { t.el.className = 'teampanel stylepanel'; bubble.prepend(t.el); }
  t.data = { ...d, tasks: d.tasks || [] };
  const head = { running: 'working', paused: 'paused', done: 'learned', needs_you: 'needs you', stopped: 'stopped' }[d.state] || d.state;
  t.el.innerHTML = `<div class="th">Competitor style <small>${esc(d.name || '')} · ${esc(head)}</small></div>
    ${d.state === 'paused' ? `<div class="tpause"><span style="flex:1">${esc(d.paused_why || '')}</span><button class="btn" data-resume>Resume</button></div>` : ''}
    ${(d.stages || []).map(s => `<div class="trow"><span class="tst ${s.status === 'passed' ? 'done' : s.status}">${STYLE_STAGE[s.status] || s.status}</span>
        <span class="tt">${esc(s.title)}</span><span class="ta">${esc(s.detail || '')}</span></div>`).join('')}
    ${(d.videos || []).map(v => `<div class="tnote">${esc(v.title)} - ${v.beats_per_min ?? '?'} visible changes/min, ${v.cuts_per_min ?? '?'} hard cuts/min</div>`).join('')}
    ${(d.problems || []).map(p => `<div class="tnote" style="color:var(${p.note ? '--dim' : '--warn'})">${esc(p.link)}: ${esc(p.why)}</div>`).join('')}
    ${d.state === 'done' ? `<div class="tnote">Open <b>Competitors</b> to read the profile and use it as guidance.</div>` : ''}`;
  const r = t.el.querySelector('[data-resume]');
  if (r) r.onclick = async () => { r.disabled = true; try { await api(`/api/projects/${pid}/team/resume`, { method: 'POST' }); } catch (e) { toast(e.message, true); } };
  return t;
}
