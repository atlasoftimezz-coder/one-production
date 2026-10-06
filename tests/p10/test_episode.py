"""P10 episode run, Fusion since F4: script -> voice -> Fusion visuals -> the owner's render -> package.

Stand-in AI turns, voice job and Resolve (the runner's answers), but the REAL Fusion service (art and
shot checks, shot timing from the spoken words), gates and packaging. Covers: a seeded bad take stops
at the voice gate; visuals need every shot built and the master's review; the run waits for the
owner's render and resumes from the file (also after a restart); the rendered video must pass the
video gate; the visuals worker is always a Claude account; nothing moves past a failed gate."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import media
from app.agent.episode import EpisodeConfig, EpisodeRun
from app.agent.runs import Run
from app.fusion import Fusion
from app.gates_api import Gates, VideoCheck, VoiceCheck
from app.packaging import Packaging
from app.scripts import read_script, stats
from app.storage import Store
from tests.p9.test_team import Deps as TeamDeps, acc, ok
from tests.p10.media_fixtures import FF, master, needs_ffmpeg, png, srt, video, voice

pytestmark = needs_ffmpeg
SCRIPT = "## Hydrogen\nIn the beginning there was hydrogen and plenty of it.\n## Helium\nThen came helium and lithium born in the first minutes.\n" \
         "## A bad year\nIn nineteen eighty six a reactor failed.\n## The end\nThe end of the story for now.\n"
CUES = [(0, 6, 'In the beginning there was hydrogen and plenty of it'), (6, 12, 'Then came helium and lithium born in the first minutes'),
        (12, 18, 'In nineteen eighty six a reactor failed'), (18, 24, 'The end of the story for now')]
ART = {"concept": "Periodic-table cards on warm paper: each element is a tile that lights up when named.",
       "palette": {"bg": "#F3EEE3", "ink": "#1B1A17", "accent": "#C8892B"}, "fonts": {"heading": "Cambria", "body": "Inter"},
       "motion": "tiles rise with a soft overshoot, 6-frame stagger, slow push", "three_d": False}
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}"><rect width="{w}" height="{h}" fill="{c}"/>'
       '<text x="50%" y="55%" font-size="120" text-anchor="middle" font-family="Arial">{t}</text></svg>')


class FakeResolve:
    """Answers like the runner: builds 'projects', exports a still per requested frame of built shots."""

    def __init__(self):
        self.enabled = True
        self.built: set[str] = set()          # shots the AI "scripted"
        self.projects: dict = {}
        self.requests: list[dict] = []

    def probe(self):
        return {'available': True, 'version': '21.1.0.17', 'product': 'DaVinci Resolve Studio'}

    def call(self, request, timeout):
        self.requests.append(request)
        spec = request['spec']
        if request['action'] == 'fusion_build':
            self.projects[spec['project_name']] = spec
            return {'passed': True, 'shots': [{k: s[k] for k in ('name', 'start', 'frames')} for s in spec['shots']]}
        stills, empty = [], []
        for s in spec['shots']:
            if s['name'] not in self.built:
                empty.append(s['name'])
                continue
            for f in s['at']:
                p = Path(spec['out_dir']) / f"{s['name']}_f{f:04d}.png"
                png(p, 32, 18)
                stills.append(str(p))
        return {'passed': True, 'stills': stills, 'empty': empty}


class Deps(TeamDeps):
    def __init__(self, tmp_path, store, pid, *, wer=0.02, anchors=('helium', 'nineteen eighty six', 'the end'), skip_first=False,
                 title='Every Element: Hydrogen to Reactors', review=None, accounts=None, ready=(True, '')):
        super().__init__(tmp_path, accounts or [acc('claude-a', 'claude', 0)], [], review=review)
        self.store, self.pid, self.files = store, pid, store.files_dir(pid)
        self.wer, self.anchors, self.skip_first, self.title, self.ready_ = wer, anchors, skip_first, title, ready
        self.poll, self.jobs, self.visual_turns = 0.01, {}, 0
        self.worker = self.turn
        self.gates = Gates(store, tmp_path, SimpleNamespace(meta=self.meta), SimpleNamespace(get=self.voice_job))
        self.resolve = FakeResolve()
        self.fusion = Fusion(store, tmp_path, self.resolve, SimpleNamespace(resolve_mcp=True))
        self.packager = Packaging(store, tmp_path, self.gates)

    async def meta(self):
        return {'voices': ['ref.wav'], 'voices_dir': str(self.files)}

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

    def fusion_ready(self):
        return self.ready_

    def fusion_stills(self, state):
        return self.fusion.stills(self.pid, state)

    async def video_gate(self, video_name, wav):
        return await self.gates.video(self.pid, VideoCheck(video=video_name, voiceover=wav))

    def package(self, spec):
        return self.packager.build(self.pid, spec)

    # the AI turns: do what the prompt asks for -----------------------------------
    async def turn(self, deps, a, sub, prompt):
        base = self.files / 'work' / 'ep-1'
        (base / 'art').mkdir(parents=True, exist_ok=True)
        if 'Fusion page' in prompt and 'Make the visuals' in prompt:
            self.visual_turns += 1
            (base / 'art.json').write_text(json.dumps(ART), encoding='utf-8')
            shots = [{'idea': 'title card'}] + [{'anchor': x, 'idea': f'shot about {x}'} for x in self.anchors]
            (base / 'shots.json').write_text(json.dumps({'shots': shots}), encoding='utf-8')
            setup = await asyncio.to_thread(self.fusion.setup, self.pid, 'work/ep-1/shots.json', 'work/ep-1/art.json',
                                            'vo.wav', 'vo.srt', '', 'Ep 1')       # the fusion_setup tool
            names = [s['name'] for s in setup['shots']]
            todo = names[:-1] if self.skip_first and self.visual_turns == 1 else names
            self.resolve.built.update(todo)                                       # "scripted" through the MCP
            return ok(f'Built {len(todo)} of {len(names)} shots.', 6000, 4000)
        if 'Package the finished episode' in prompt:
            (base / 'art' / 'thumbnail.svg').write_text(SVG.format(w=1280, h=720, c='#ffd400', t='H'), encoding='utf-8')
            vid = prompt.split('Video: ', 1)[1].split('. Script', 1)[0]
            (base / 'package.json').write_text(json.dumps({
                'name': 'ep-1', 'video': vid, 'subtitles': 'vo.srt', 'script': 'script.md', 'title': self.title,
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


def make(tmp_path, store, pid, deps, **cfg):
    run = Run(pid, store.files_dir(pid))
    spec = {'name': 'Ep 1', 'script': 'script.md', 'voice': 'ref.wav', 'voice_job': '', 'size': '1920x1080'}
    return EpisodeRun(pid, spec, run, deps, EpisodeConfig(poll=0.01, **cfg), state_file=tmp_path / 'episode.json'), run


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 300))


def status(snap):
    return {s['id']: s['status'] for s in snap['stages']}


def owner_renders(store, pid, name='render.mp4', seconds=24.0, **kw):
    files = store.files_dir(pid)
    video(files / name, files / 'vo.wav', seconds, **kw)
    return name


def test_full_episode_waits_for_the_owners_render_then_packages(tmp_path, setup):
    store, pid = setup
    deps = Deps(tmp_path, store, pid)
    ep, r = make(tmp_path, store, pid, deps)
    snap = run(ep.go())
    assert snap['state'] == 'waiting_render', (snap['result'], [s['detail'] for s in snap['stages']])
    assert status(snap) == {'script': 'passed', 'voice': 'passed', 'visuals': 'passed', 'edit': 'waiting', 'package': 'waiting'}
    project = snap['result']['project']
    assert project.startswith('Ep 1 - One Production') and 'Render the "Episode" timeline' in ep.stage('edit')['detail']
    # the runner got a 60 fps project with shots cut at the spoken words, and the art direction
    build = deps.resolve.requests[0]['spec']
    assert build['fps'] == 60 and build['style']['fonts']['heading'] == 'Cambria'
    starts = [s['start'] for s in build['shots']]
    assert starts[0] == 0 and starts == sorted(starts) and sum(s['frames'] for s in build['shots']) == pytest.approx(24 * 60, abs=2)
    visual_prompt = next(c[4] for c in deps.calls if c[0] == 'worker')
    assert 'from op_kit import *' in visual_prompt and 'fusion_setup' in visual_prompt and 'art.json' in visual_prompt
    stills = sorted((store.files_dir(pid) / 'work' / 'ep-1' / 'stills').glob('*.png'))
    assert len(stills) == 2 * len(build['shots'])
    # the owner renders in Resolve and picks the file
    snap = run(ep.resume(owner_renders(store, pid)))
    assert snap['state'] == 'done' and set(status(snap).values()) == {'passed'}, snap['result']
    bundle = store.files_dir(pid) / 'episode' / 'ep-1'
    assert json.loads((bundle / 'metadata.json').read_text(encoding='utf-8'))['title'].startswith('Every Element')
    roles = [c[0] for c in deps.calls]
    assert roles.count('worker') == 2 and roles.count('master') == 3            # 2 reviews + the final report
    assert snap['final'] and json.loads((tmp_path / 'episode.json').read_text())['state'] == 'done'


def test_a_bad_render_is_stopped_by_the_video_gate(tmp_path, setup):
    store, pid = setup
    ep, _ = make(tmp_path, store, pid, Deps(tmp_path, store, pid))
    assert run(ep.go())['state'] == 'waiting_render'
    snap = run(ep.resume(owner_renders(store, pid, 'short.mp4', seconds=15.0)))
    assert snap['state'] == 'needs_you' and snap['result']['stage'] == 'edit' and 'video gate' in snap['result']['why']
    assert status(snap)['package'] == 'waiting'


def test_the_waiting_run_resumes_from_its_snapshot_after_a_restart(tmp_path, setup):
    store, pid = setup
    deps = Deps(tmp_path, store, pid)
    ep, _ = make(tmp_path, store, pid, deps)
    run(ep.go())
    snap = json.loads((tmp_path / 'episode.json').read_text(encoding='utf-8'))
    assert snap['state'] == 'waiting_render' and snap['vo']['wav'] == 'vo.wav'
    again = EpisodeRun.from_snapshot(pid, snap, Run(pid, store.files_dir(pid)), deps, EpisodeConfig(poll=0.01),
                                     state_file=tmp_path / 'episode.json')
    done = run(again.resume(owner_renders(store, pid)))
    assert done['state'] == 'done' and done['stages'][2]['status'] == 'passed'


def test_seeded_bad_take_stops_at_the_voice_gate(tmp_path, setup):
    store, pid = setup
    deps = Deps(tmp_path, store, pid, wer=0.2)
    ep, _ = make(tmp_path, store, pid, deps)
    snap = run(ep.go())
    assert snap['state'] == 'needs_you' and snap['result']['stage'] == 'voice' and 'read-check' in snap['result']['why']
    assert status(snap) == {'script': 'passed', 'voice': 'needs_you', 'visuals': 'waiting', 'edit': 'waiting', 'package': 'waiting'}
    assert not deps.calls and not deps.resolve.requests                        # no AI or Resolve after a failed gate


def test_an_unbuilt_shot_goes_back_to_the_worker(tmp_path, setup):
    store, pid = setup
    deps = Deps(tmp_path, store, pid, skip_first=True)
    ep, _ = make(tmp_path, store, pid, deps)
    snap = run(ep.go())
    assert snap['state'] == 'waiting_render' and deps.visual_turns == 2
    second = [c[4] for c in deps.calls if c[0] == 'worker'][1]
    assert 'automatic check failed' in second and 'still empty' in second and 'shot-004' in second


def test_fusion_must_be_ready_and_the_visuals_worker_is_a_claude_account(tmp_path, setup):
    store, pid = setup
    off = Deps(tmp_path, store, pid, ready=(False, 'The Resolve MCP switch is off'))
    ep, _ = make(tmp_path, store, pid, off)
    snap = run(ep.go())
    assert snap['state'] == 'needs_you' and snap['result'] == {'stage': 'visuals', 'why': 'The Resolve MCP switch is off'}
    store2 = store.create_project('Mixed')['id']
    (store.files_dir(store2) / 'script.md').write_text(SCRIPT, encoding='utf-8')
    voice(store.files_dir(store2) / 'ref.wav', 120, 10)
    mixed = Deps(tmp_path, store, store2, accounts=[acc('codex-a', 'codex', 0), acc('claude-b', 'claude', 1)])
    ep, _ = make(tmp_path, store, store2, mixed)
    run(ep.go())
    visual = next(c for c in mixed.calls if c[0] == 'worker')
    assert visual[1] == 'claude-b'                                              # never the Codex account (no Resolve MCP)


def test_gate_misses_and_master_rounds_are_capped(tmp_path, setup):
    store, pid = setup
    deps = Deps(tmp_path, store, pid, title='x' * 120)                          # package gate fails every time
    ep, _ = make(tmp_path, store, pid, deps, gate_rounds=2)
    assert run(ep.go())['state'] == 'waiting_render'
    snap = run(ep.resume(owner_renders(store, pid)))
    assert snap['state'] == 'needs_you' and snap['result']['stage'] == 'package'
    assert 'title length' in snap['result']['why'] and 'after 2 tries' in snap['result']['why']
    other = store.create_project('Other')['id']
    (store.files_dir(other) / 'script.md').write_text(SCRIPT, encoding='utf-8')
    voice(store.files_dir(other) / 'ref.wav', 120, 10)
    picky = Deps(tmp_path, store, other, review=lambda title, n: {'verdict': 'revise', 'notes': 'more detail'})
    ep, _ = make(tmp_path, store, other, picky)
    snap = run(ep.go())
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
    # the rendered-file route: the file must be in the project, and an episode must be waiting
    assert 'not a file' in client.post(base + '/render', json={'video': 'nope.mp4'}).json()['detail']
    client.post(f'/api/projects/{pid}/files', files={'files': ('r.mp4', b'0')})
    r = client.post(base + '/render', json={'video': 'r.mp4'})
    assert r.status_code == 409 and 'waiting' in r.json()['detail']
