"""P10 numeric stage gates on synthetic media. Covers PLAN.txt's AUTO items: a seeded bad take is
rejected, a 6-semitone pitch shift fails the voice gate, the final video must match the voiceover
+/- 0.5 s with A/V drift < 40 ms and no black frames."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import gates, media
from app.agent.gate_tools import tools
from app.agent.policy import decide
from app.gates_api import Gates
from app.storage import NotFound
from tests.p10.media_fixtures import FF, ff, master, needs_ffmpeg, png, srt, video, voice

pytestmark = needs_ffmpeg
GOOD_SECTIONS = [{'title': 'Intro', 'wer': 0.0}, {'title': 'Body', 'wer': 0.03}]


@pytest.fixture(scope='module')
def kit(tmp_path_factory):
    d = tmp_path_factory.mktemp('media')
    ref = voice(d / 'ref.wav', 120, 10)
    vo = master(voice(d / 'raw.wav', 120, 8), d / 'vo.wav')
    ff('-i', vo, '-af', 'rubberband=pitch=1.41421356', d / 'up6-raw.wav')        # +6 semitones
    up6 = master(d / 'up6-raw.wav', d / 'up6.wav')
    quiet = master(voice(d / 'q.wav', 120, 8), d / 'quiet.wav', lufs=-20)
    hot = d / 'hot.wav'
    ff('-i', vo, '-af', 'volume=9dB', hot)
    seconds = media.duration(FF, vo)
    return SimpleNamespace(d=d, ref=ref, vo=vo, up6=up6, quiet=quiet, hot=hot, seconds=seconds, srt=srt(d / 'vo.srt', seconds))


def failed(report):
    return {c['name'] for c in report['checks'] if c['status'] == 'fail' or (c['status'] == 'skip' and c['required'])}


def test_clean_clone_passes_every_voice_check(kit):
    r = gates.voice_gate(FF, kit.vo, srt=kit.srt, reference=kit.ref, sections=GOOD_SECTIONS, pitch_required=True)
    assert r['passed'], r['summary']
    by = {c['name']: c for c in r['checks']}
    assert abs(by['loudness']['value'] + 14) <= 0.5 and by['true peak']['value'] <= -1.0
    assert by['pitch matches the reference']['value'] <= 0.5


def test_seeded_bad_take_is_rejected(kit):
    r = gates.voice_gate(FF, kit.vo, srt=kit.srt, reference=kit.ref, sections=GOOD_SECTIONS + [{'title': 'Outro', 'wer': 0.12}])
    assert not r['passed'] and failed(r) == {'read-check (WER)'} and 'Outro' in r['summary']
    unchecked = gates.voice_gate(FF, kit.vo, srt=kit.srt, sections=[{'title': 'Intro', 'wer': None}])
    assert 'read-check (WER)' in failed(unchecked)


def test_six_semitone_pitch_shift_fails_the_voice_gate(kit):
    r = gates.voice_gate(FF, kit.up6, srt=kit.srt, reference=kit.ref, sections=GOOD_SECTIONS, pitch_required=True)
    assert not r['passed'] and failed(r) == {'pitch matches the reference'}
    off = next(c for c in r['checks'] if c['name'] == 'pitch matches the reference')['value']
    assert 5.5 <= off <= 6.5, off
    no_ref = gates.voice_gate(FF, kit.vo, srt=kit.srt, sections=GOOD_SECTIONS, pitch_required=True)
    assert failed(no_ref) == {'pitch matches the reference'}             # a clone can't skip its pitch check


def test_loudness_and_true_peak_limits(kit):
    assert 'loudness' in failed(gates.voice_gate(FF, kit.quiet, srt=kit.srt))
    hot = gates.voice_gate(FF, kit.hot, srt=kit.srt)
    assert {'loudness', 'true peak'} <= failed(hot)


def test_subtitles_must_be_complete_ordered_and_not_overlapping(kit):
    s = kit.seconds
    cases = {'subtitles do not overlap': [(0, 3, 'a'), (2.5, s, 'b')], 'subtitles have text': [(0, 3, 'a'), (3, s, '')],
             'subtitles cover the audio': [(0, s - 3, 'a')], 'subtitle timings valid': [(0, 3, 'a'), (4, 3.5, 'b'), (4, s, 'c')]}
    for name, cues in cases.items():
        r = gates.voice_gate(FF, kit.vo, srt=srt(kit.d / 'bad.srt', s, cues))
        assert name in failed(r), (name, r['summary'])
    assert 'subtitles complete' in failed(gates.voice_gate(FF, kit.vo))


def test_final_video_duration_drift_and_black_frames(kit):
    s = kit.seconds
    good = gates.video_gate(FF, video(kit.d / 'good.mp4', kit.vo, s), kit.vo)
    assert good['passed'], good['summary']
    assert next(c for c in good['checks'] if c['name'] == 'audio/video drift')['value'] < 40
    long = gates.video_gate(FF, video(kit.d / 'long.mp4', kit.vo, s + 1.2), kit.vo)
    assert 'duration matches the voiceover' in failed(long)
    black = gates.video_gate(FF, video(kit.d / 'black.mp4', kit.vo, s, black_at=3), kit.vo)
    assert failed(black) == {'no black frames'} and '3.0-4.0 s' in black['summary']
    late = gates.video_gate(FF, video(kit.d / 'late.mp4', kit.vo, s, audio_delay_ms=120), kit.vo)
    assert 'audio/video drift' in failed(late)
    small = gates.video_gate(FF, video(kit.d / 'small.mp4', kit.vo, s, size='640x360'), kit.vo)
    assert failed(small) == {'picture size'}


def test_package_gate_follows_youtube_limits(tmp_path):
    meta = {'title': 'Every Element Explained', 'description': 'From hydrogen to oganesson.', 'tags': ['science', 'elements'],
            'chapters': [{'time': '0:00', 'title': 'Intro'}, {'time': '0:45', 'title': 'Metals'}, {'time': '3:10', 'title': 'Gases'}]}
    ok = gates.package_gate(meta, video_seconds=400, thumbnail=png(tmp_path / 't.png', 1280, 720))
    assert ok['passed'], ok['summary']
    assert gates.chapters_text(meta['chapters']) == '0:00 Intro\n0:45 Metals\n3:10 Gases'
    bad = {'title': 'x' * 101, 'description': 'a <b> tag', 'tags': ['t' * 300, 'u' * 300],
           'chapters': [{'time': '0:05', 'title': 'A'}, {'time': '0:10', 'title': 'B'}]}
    r = gates.package_gate(bad, video_seconds=400, thumbnail=png(tmp_path / 's.png', 640, 360))
    assert failed(r) == {'title length', 'description length', 'tags length', 'chapters', 'thumbnail'}
    assert 'first chapter must start at 0:00' in r['summary'] and 'YouTube needs 3' in r['summary']
    late = dict(meta, chapters=meta['chapters'] + [{'time': '6:35', 'title': 'End'}])
    assert 'shorter than 10 s' in gates.package_gate(late, video_seconds=400)['summary']
    assert gates.package_gate(dict(meta, chapters=[{'time': 'soon', 'title': 'x'}]))['passed'] is False
    assert gates.package_gate(meta)['passed']                                    # thumbnail optional until packaging
    assert gates.package_gate(dict(meta, chapters=[]))['passed']                  # chapters are optional, checked when present


def test_routes_save_reports_and_agent_tool(client, kit, tmp_path):
    pid = client.post('/api/projects', json={'name': 'Gates'}).json()['id']
    for p in (kit.vo, kit.srt, kit.ref, video(kit.d / 'route.mp4', kit.vo, kit.seconds)):
        client.post(f'/api/projects/{pid}/files', files={'files': (p.name, p.read_bytes())})
    r = client.post(f'/api/projects/{pid}/gates/voice', json={'audio': 'vo.wav', 'srt': 'vo.srt', 'reference_file': 'ref.wav'})
    assert r.status_code == 200 and r.json()['passed'], r.text
    saved = client.get(f"/api/projects/{pid}/files/{r.json()['report']}")
    assert saved.status_code == 200 and json.loads(saved.content)['source'] == {'audio': 'vo.wav'}
    v = client.post(f'/api/projects/{pid}/gates/video', json={'video': 'route.mp4', 'voiceover': 'vo.wav'}).json()
    assert v['passed'], v['summary']
    p = client.post(f'/api/projects/{pid}/gates/package', json={'meta': {'title': 'T', 'chapters': [{'time': '0:00', 'title': 'A'}]},
                                                                'video': 'route.mp4'}).json()
    assert not p['passed'] and 'chapters' in p['summary']
    assert [x['stage'] for x in client.get(f'/api/projects/{pid}/gates').json()['reports']] == ['package', 'video', 'voice']
    assert client.post(f'/api/projects/{pid}/gates/video', json={'video': '../../x.mp4', 'voiceover': 'vo.wav'}).status_code == 400
    assert client.post(f'/api/projects/{pid}/gates/voice', json={'job_id': 'nope'}).status_code == 400
    # the agents' tool, with a finished voice job whose reference comes from the voice app
    files_dir = client.data_dir / 'projects' / pid / 'files'
    job = {'id': 'j1', 'project': pid, 'status': 'done', 'voice': 'ref.wav', 'files': ['vo.wav', 'vo.srt'], 'sections': GOOD_SECTIONS}

    class Svc:
        async def meta(self):
            return {'voices': ['ref.wav'], 'voices_dir': str(files_dir)}
    def file_path(pid_, name):
        if not (files_dir / name).is_file():
            raise NotFound(name)
        return files_dir / name
    g = Gates(SimpleNamespace(file_path=file_path, files_dir=lambda pid_: files_dir), tmp_path, Svc(),
              SimpleNamespace(get=lambda jid: job if jid == 'j1' else None))
    tool = tools(SimpleNamespace(gates=g, pid=pid))[0]
    result = json.loads(asyncio.run(tool.handler({'stage': 'voice', 'job_id': 'j1'})))
    assert result['passed'] and result['report'].startswith('gates/voice-')
    assert json.loads(asyncio.run(tool.handler({'stage': 'nope'})))['error']
    assert decide('quality_gate', {}, tmp_path)[0] == 'allow'
