"""P10 ffmpeg editor: edit list -> mp4 fitted to the voiceover to the frame, checked by the video gate.
Synthetic media only; renders are tiny (720p, ~12 s)."""
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import editor as E, gates, media
from app.agent.policy import HEAVY, decide
from tests.p10.media_fixtures import FF, ff, master, needs_ffmpeg, png, srt, voice

pytestmark = needs_ffmpeg


@pytest.fixture
def proj(client, tmp_path):
    pid = client.post('/api/projects', json={'name': 'Edit'}).json()['id']
    files = client.data_dir / 'projects' / pid / 'files'
    (files / 'art').mkdir()
    vo = master(voice(tmp_path / 'raw.wav', 120, 12), files / 'vo.wav')
    png(files / 'art' / 'a.png', 1600, 900)
    png(files / 'art' / 'b.png', 900, 1600)                     # portrait: cropped to fill
    ff('-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25:duration=2', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', files / 'clip.mp4')
    ff('-f', 'lavfi', '-i', 'sine=frequency=330:duration=3', '-ar', 48000, files / 'bed.wav')
    seconds = media.duration(FF, vo)
    srt(files / 'vo.srt', seconds)
    return SimpleNamespace(c=client, pid=pid, files=files, seconds=seconds, base=f'/api/projects/{pid}/edit')


def write_edit(proj, name='edit.json', **change):
    s = proj.seconds
    edit = {'voiceover': 'vo.wav', 'size': '1280x720', 'fps': 30, 'output': 'episode.mp4',
            'visuals': [{'card': {'text': 'Every Element' + chr(10) + 'Explained', 'style': 'title'}, 'seconds': 1.5},
                        {'file': 'art/a.png', 'seconds': 2, 'motion': 'push'},
                        {'file': 'clip.mp4', 'seconds': 3, 'from': 0.5},            # 1.5 s clip held on its last frame
                        {'card': {'text': '1986', 'style': 'date'}, 'seconds': 1},
                        {'file': 'art/b.png', 'seconds': s - 7.5 - 0.6, 'motion': 'pan_right'}]}   # 0.6 s short: fitted
    edit.update(change)
    (proj.files / name).write_text(json.dumps(edit), encoding='utf-8')
    return name


def wait(proj, job_id, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        job = proj.c.get(f'{proj.base}/jobs/{job_id}').json()
        if job['status'] != 'rendering':
            return job
        time.sleep(0.3)
    raise AssertionError('render did not finish')


def test_render_needs_approval_fits_to_the_frame_and_passes_the_video_gate(proj):
    name = write_edit(proj)
    checked = proj.c.post(f'{proj.base}/check', json={'edit': name}).json()
    assert checked['visuals'] == 5 and checked['fitted_by'] == pytest.approx(0.6, abs=0.01)
    need = proj.c.post(f'{proj.base}/render', json={'edit': name})
    assert need.status_code == 409 and need.json()['needs_approval']['tool'] == 'video_render'
    job = proj.c.post(f'{proj.base}/render', json={'edit': name, 'approve': 'once'}).json()
    job = wait(proj, job['id'])
    assert job['status'] == 'done' and job['output'] == 'episode.mp4', job
    assert job['gate']['passed'], job['gate']['summary']
    out = proj.files / 'episode.mp4'
    info = media.probe(FF, out)
    v = next(s for s in info['streams'] if s['codec_type'] == 'video')
    assert (v['width'], v['height']) == (1280, 720)
    frames = int(media.run([media.ffprobe_for(FF), '-v', 'error', '-count_packets', '-select_streams', 'v:0',
                            '-show_entries', 'stream=nb_read_packets', '-of', 'csv=p=0', str(out)]).stdout.strip())
    assert frames == round(proj.seconds * 30)
    assert (proj.files / job['gate']['report']).is_file()
    assert not (proj.c.data_dir / 'cache' / 'render' / job['id']).exists()        # work folder cleaned
    assert proj.c.post(f'{proj.base}/render', json={'edit': name}).status_code == 409   # "once" is not remembered


def test_music_is_ducked_and_mastered_and_subtitles_burn_in(proj):
    name = write_edit(proj, 'mixed.json', output='mixed.mp4', music={'file': 'bed.wav', 'db': -18, 'duck': True},
                      subtitles={'file': 'vo.srt', 'burn': True})
    job = wait(proj, proj.c.post(f'{proj.base}/render', json={'edit': name, 'approve': 'always'}).json()['id'])
    assert job['status'] == 'done' and job['gate']['passed'], job
    loud = media.loudness(FF, proj.files / 'mixed.mp4')
    assert abs(loud['lufs'] + 14) <= 0.5 and loud['true_peak'] <= -1.0, loud
    assert proj.c.post(f'{proj.base}/render', json={'edit': write_edit(proj)}).status_code == 200   # "always" remembered


def test_edit_list_validation(proj):
    def bad(**change):
        r = proj.c.post(f'{proj.base}/check', json={'edit': write_edit(proj, 'bad.json', **change)})
        assert r.status_code == 400, r.text
        return r.json()['detail']
    s = proj.seconds
    assert 'must be within 2 s' in bad(visuals=[{'file': 'art/a.png', 'seconds': s - 3}])
    assert 'motion must be one of' in bad(visuals=[{'file': 'art/a.png', 'seconds': s, 'motion': 'spin'}])
    assert 'not a file in this project' in bad(visuals=[{'file': '../../secret.png', 'seconds': s}])
    assert 'not a picture or video clip' in bad(visuals=[{'file': 'vo.srt', 'seconds': s}])
    assert 'at most 4 lines' in bad(visuals=[{'card': {'text': chr(10).join('abcde')}, 'seconds': s}])
    assert 'card style' in bad(visuals=[{'card': {'text': 'x', 'style': 'neon'}, 'seconds': s}])
    assert 'either a file or a card' in bad(visuals=[{'seconds': s}])
    assert 'fps must be one of' in bad(fps=29)
    assert 'music db' in bad(music={'file': 'bed.wav', 'db': 0})
    assert 'voiceover must be an audio file' in bad(voiceover='art/a.png')
    assert proj.c.post(f'{proj.base}/check', json={'edit': 'missing.json'}).status_code == 400
    assert proj.c.get(f'{proj.base}/jobs/nope').status_code == 400


def test_frame_plan_never_drops_a_visual():
    file_of = lambda name: Path(name)
    p = E.plan({'visuals': [{'card': {'text': 'a'}, 'seconds': 0.2}] * 3 + [{'card': {'text': 'b'}, 'seconds': 9}]}, file_of, 10.4)
    assert [v['frames'] for v in p['visuals']] == [6, 6, 6, 294] and sum(v['frames'] for v in p['visuals']) == 312


def test_agents_render_through_the_heavy_job_gate(tmp_path):
    assert decide('render_edit', {'edit': 'e.json'}, tmp_path)[0] == 'gate'
    assert decide('mcp__studio__render_edit', {}, tmp_path)[0] == 'gate'
    assert decide('edit_status', {}, tmp_path)[0] == 'allow'
    assert HEAVY['render_edit'] == 'video_render' and HEAVY['make_voiceover'] == 'voiceover'
