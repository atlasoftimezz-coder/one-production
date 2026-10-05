"""P10 episode run: script -> voice -> visuals -> edit -> package with stand-in AI turns and voice job,
but the REAL gates, animation, ffmpeg editor and packaging. Covers the plan's AUTO items end to end:
a seeded bad take stops the run at the voice gate; the final video matches the voiceover +/- 0.5 s
with A/V drift < 40 ms and no black frames; a failed check goes back to the worker; nothing moves
past a failed gate."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import media
from app.agent.episode import EpisodeConfig, EpisodeRun
from app.agent.runs import Run
from app.animation import Animation, find_browser
from app.editor import Editor
from app.gates_api import Gates, VoiceCheck
from app.packaging import Packaging
from app.scripts import read_script, stats
from app.storage import Store
from tests.p9.test_team import Deps as TeamDeps, acc, ok
from tests.p10.media_fixtures import FF, master, needs_ffmpeg, srt, voice

pytestmark = [needs_ffmpeg, pytest.mark.skipif(find_browser() is None, reason='Chrome or Edge is not installed')]
SCRIPT = "## Hydrogen\nIn the beginning there was hydrogen and plenty of it.\n## Helium\nThen came helium and lithium born in the first minutes.\n" \
         "## A bad year\nIn nineteen eighty six a reactor failed.\n## The end\nThe end of the story for now.\n"
CUES = [(0, 6, 'In the beginning there was hydrogen and plenty of it'), (6, 12, 'Then came helium and lithium born in the first minutes'),
        (12, 18, 'In nineteen eighty six a reactor failed'), (18, 24, 'The end of the story for now')]
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}"><rect width="{w}" height="{h}" fill="{c}"/>'
       '<text x="50%" y="55%" font-size="120" text-anchor="middle" font-family="Arial">{t}</text></svg>')


class Deps(TeamDeps):
    """Stand-in accounts and AI turns (tests/p9) + the app's real media stages on a temp project."""

    def __init__(self, tmp_path, store, pid, *, wer=0.02, anchors=('helium', 'nineteen eighty six', 'the end'), bad_first=False,
                 title='Every Element: Hydrogen to Reactors', review=None):
        super().__init__(tmp_path, [acc('claude-a', 'claude', 0)], [], review=review)
        self.store, self.pid, self.files = store, pid, store.files_dir(pid)
        self.wer, self.anchors, self.bad_first, self.title = wer, anchors, bad_first, title
        self.poll, self.jobs, self.visual_turns = 0.01, {}, 0
        self.worker = self.turn
        self.gates = Gates(store, tmp_path, SimpleNamespace(meta=self.meta), SimpleNamespace(get=self.voice_job))
        self.anim, self.editor = Animation(store, tmp_path), Editor(store, tmp_path, self.gates)
        self.packager = Packaging(store, tmp_path, self.gates)

    async def meta(self):
        return {'voices': ['ref.wav'], 'voices_dir': str(self.files)}

    # app stages ------------------------------------------------------------
    def script_stats(self, name):
        return stats(read_script(self.store.file_path(self.pid, name)))

    def voice_job(self, jid):
        return self.jobs.get(jid)

    async def start_voice(self, spec):
        job = {'id': 'v1', 'project': self.pid, 'status': 'rendering', 'step': 'voicing', 'voice': 'ref.wav', 'files': [],
               'sections': [{'title': t, 'wer': self.wer if n == 2 else 0.0} for n, t in enumerate('abcd')]}
        self.jobs['v1'] = job
        async def finish():
            await asyncio.sleep(0.05)
            master(voice(self.files / 'raw.wav', 120, 24), self.files / 'vo.wav')
            srt(self.files / 'vo.srt', 24, CUES)
            job.update(status='done', files=['vo.mp3', 'vo.wav', 'vo.srt'])
        asyncio.get_running_loop().create_task(finish())
        return dict(job)

    async def voice_gate(self, jid):
        return await self.gates.voice(self.pid, VoiceCheck(job_id=jid))

    def duration(self, name):
        return media.duration(FF, self.store.file_path(self.pid, name))

    def build_animation(self, shots, edit):
        return self.anim.build(self.pid, shots, edit)

    def start_render(self, edit):
        return self.editor.start(self.pid, edit)

    def render_job(self, jid):
        return self.editor.get(self.pid, jid)

    def cancel_render(self, jid):
        self.editor.cancel(self.pid, jid)

    def package(self, spec):
        return self.packager.build(self.pid, spec)

    # the AI turns: write what the prompt asks for -------------------------
    async def turn(self, deps, a, sub, prompt):
        base = self.files / 'work' / 'ep-1'
        (base / 'art').mkdir(parents=True, exist_ok=True)
        if 'Draw the visuals' in prompt:
            self.visual_turns += 1
            for n, colour in enumerate(('#cfe8ff', '#ffe9b3', '#d8f5d0', '#f6d0e0')):
                (base / 'art' / f's{n}.svg').write_text(SVG.format(w=1920, h=1080, c=colour, t=f'S{n}'), encoding='utf-8')
            anchors = ('oxygen',) + self.anchors[1:] if self.bad_first and self.visual_turns == 1 else self.anchors
            shots = [{'svg': 'work/ep-1/art/s0.svg', 'motion': 'push'}] + [
                {'svg': f'work/ep-1/art/s{n + 1}.svg', 'anchor': a, 'motion': 'pan_right'} for n, a in enumerate(anchors)]
            spec = json.loads(prompt.split('2. Write work/ep-1/shots.json: ', 1)[1].split('. The first shot', 1)[0].replace('{"svg": "...", ', '{"svg": "x", ').split(', "shots"')[0] + '}')
            (base / 'shots.json').write_text(json.dumps({**spec, 'size': '1280x720', 'shots': shots}), encoding='utf-8')
            return ok(f'Drew {len(shots)} shots.', 4000, 3000)
        if 'Package the finished episode' in prompt:
            (base / 'art' / 'thumbnail.svg').write_text(SVG.format(w=1280, h=720, c='#ffd400', t='H'), encoding='utf-8')
            video = prompt.split('Video: ', 1)[1].split('. Script', 1)[0]
            (base / 'package.json').write_text(json.dumps({
                'name': 'ep-1', 'video': video, 'subtitles': 'vo.srt', 'script': 'script.md', 'title': self.title,
                'title_options': [self.title, 'From Hydrogen to Reactors'], 'description': 'The first elements, and a bad year.',
                'tags': ['science'], 'thumbnail_svg': 'work/ep-1/art/thumbnail.svg'}), encoding='utf-8')
            return ok('Packaged.', 2000, 800)
        return ok('?', 10, 10)


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / 'data')
    pid = store.create_project('Episode')['id']
    files = store.files_dir(pid)
    (files / 'script.md').write_text(SCRIPT, encoding='utf-8')
    voice(files / 'ref.wav', 120, 10)
    return store, pid


def go(tmp_path, store, pid, deps, **cfg):
    run = Run(pid, store.files_dir(pid))
    spec = {'name': 'Ep 1', 'script': 'script.md', 'voice': 'ref.wav', 'voice_job': '', 'size': '1280x720'}
    ep = EpisodeRun(pid, spec, run, deps, EpisodeConfig(poll=0.01, **cfg), state_file=tmp_path / 'episode.json')
    return asyncio.run(asyncio.wait_for(ep.go(), 300)), run


def status(snap):
    return {s['id']: s['status'] for s in snap['stages']}


def test_full_episode_passes_every_gate(tmp_path, setup):
    store, pid = setup
    deps = Deps(tmp_path, store, pid)
    snap, run = go(tmp_path, store, pid, deps)
    assert snap['state'] == 'done', (snap['result'], [s['detail'] for s in snap['stages']])
    assert set(status(snap).values()) == {'passed'}
    files = store.files_dir(pid)
    video = files / 'work' / 'ep-1' / 'ep-1.mp4'
    report = json.loads((files / next(s for s in snap['stages'] if s['id'] == 'edit')['report']).read_text())
    by = {c['name']: c for c in report['checks']}
    assert abs(by['duration matches the voiceover']['value']) <= 0.5 and by['audio/video drift']['value'] < 40
    assert by['no black frames']['status'] == 'pass' and media.duration(FF, video) == pytest.approx(24, abs=0.5)
    bundle = files / 'episode' / 'ep-1'
    meta = json.loads((bundle / 'metadata.json').read_text(encoding='utf-8'))
    assert meta['title'].startswith('Every Element') and (bundle / 'thumbnail.png').is_file()
    roles = [c[0] for c in deps.calls]
    assert roles.count('worker') == 2 and roles.count('master') == 3            # 2 reviews + the final report
    assert snap['final'] and json.loads((tmp_path / 'episode.json').read_text())['state'] == 'done'
    assert any(e['t'] == 'episode' for e in run.events)


def test_seeded_bad_take_stops_at_the_voice_gate(tmp_path, setup):
    store, pid = setup
    deps = Deps(tmp_path, store, pid, wer=0.2)
    snap, _ = go(tmp_path, store, pid, deps)
    assert snap['state'] == 'needs_you' and snap['result']['stage'] == 'voice' and 'read-check' in snap['result']['why']
    assert status(snap) == {'script': 'passed', 'voice': 'needs_you', 'visuals': 'waiting', 'edit': 'waiting', 'package': 'waiting'}
    assert not deps.calls                                                       # no AI spent after a failed gate


def test_failed_check_goes_back_to_the_worker_with_the_numbers(tmp_path, setup):
    store, pid = setup
    deps = Deps(tmp_path, store, pid, bad_first=True)
    snap, _ = go(tmp_path, store, pid, deps)
    assert snap['state'] == 'done' and deps.visual_turns == 2
    second = [c[4] for c in deps.calls if c[0] == 'worker' and 'Draw the visuals' in c[4]][1]
    assert 'automatic check failed' in second and 'oxygen' in second
    assert any('check failed' in n for n in snap['notes'])


def test_gate_misses_and_master_rounds_are_capped(tmp_path, setup):
    store, pid = setup
    deps = Deps(tmp_path, store, pid, title='x' * 120)                          # package gate fails every time
    snap, _ = go(tmp_path, store, pid, deps, gate_rounds=2)
    assert snap['state'] == 'needs_you' and snap['result']['stage'] == 'package'
    assert 'title length' in snap['result']['why'] and 'after 2 tries' in snap['result']['why']
    store2_pid = store.create_project('Other')['id']
    (store.files_dir(store2_pid) / 'script.md').write_text(SCRIPT, encoding='utf-8')
    voice(store.files_dir(store2_pid) / 'ref.wav', 120, 10)
    picky = Deps(tmp_path, store, store2_pid, review=lambda title, n: {'verdict': 'revise', 'notes': 'more detail'})
    snap, _ = go(tmp_path, store, store2_pid, picky)
    assert snap['state'] == 'needs_you' and snap['result']['stage'] == 'visuals' and 'review rounds' in snap['result']['why']


def test_route_checks_inputs_before_anything_runs(client):
    pid = client.post('/api/projects', json={'name': 'Ep'}).json()['id']
    base = f'/api/projects/{pid}/episode'
    assert 'name' in client.post(base, json={'name': ' ', 'script': 's.md'}).json()['detail']
    assert 'not a file' in client.post(base, json={'name': 'E', 'script': 'missing.md', 'voice': 'v'}).json()['detail']
    client.post(f'/api/projects/{pid}/files', files={'files': ('s.md', b'## A\nHello there.')})
    assert 'choose a voice' in client.post(base, json={'name': 'E', 'script': 's.md'}).json()['detail']
    assert 'not a finished one' in client.post(base, json={'name': 'E', 'script': 's.md', 'voice_job': 'nope'}).json()['detail']
    assert 'needs at least one' in client.post(base, json={'name': 'E', 'script': 's.md', 'voice': 'v'}).json()['detail']
    assert client.get(base).json() == {'running': False, 'episode': None}
