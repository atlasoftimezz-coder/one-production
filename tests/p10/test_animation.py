"""P10 animation: SVG shots -> PNG frames (the PC's Chrome/Edge) -> shots timed to the spoken words ->
edit list -> render -> video gate. Synthetic voice and subtitles; tiny SVGs."""
import json
import subprocess
import time

import pytest

from app import animation as A, gates, media
from app.agent.policy import decide
from app.editor import EditError
from tests.p10.media_fixtures import FF, master, needs_ffmpeg, srt, voice

pytestmark = needs_ffmpeg
CUES = [(0, 3, 'In the beginning there was hydrogen'), (3, 6, 'Then came helium and lithium'),
        (6, 9, 'In nineteen eighty six'), (9, 12, 'the end of the story')]


def drawing(colour, label):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080"><rect width="1920" height="1080" fill="{colour}"/>'
            f'<circle cx="960" cy="420" r="120" fill="none" stroke="#111" stroke-width="12"/>'
            f'<text x="960" y="960" font-family="Arial" font-size="90" text-anchor="middle" fill="#d62828">{label}</text></svg>')


def test_anchors_are_found_in_order_and_missing_ones_are_named():
    cues = [{'start': a, 'end': b, 'text': t} for a, b, t in CUES]
    starts, missing = A.anchor_times([None, 'helium', 'nineteen eighty six', 'the end'], cues)
    assert missing == [] and starts == pytest.approx([0, 4.2, 6.75, 9.0])
    # "in" occurs twice: searching continues after the previous shot's words
    starts, _ = A.anchor_times([None, 'hydrogen', 'in'], cues)
    assert starts[2] == pytest.approx(6.0)
    _, missing = A.anchor_times([None, 'oxygen', ''], cues)
    assert 'shot 2: "oxygen"' in missing[0] and 'shot 3 has no anchor' in missing[1]


@pytest.mark.parametrize('body, reason', [
    ('<script>alert(1)</script>', '<script>'),
    ('<rect onload="x()" width="1" height="1"/>', 'event handlers'),
    ('<image href="https://example.com/a.png"/>', 'links outside'),
    ('<image href="file:///C:/secret.png"/>', 'links outside'),
    ('<style>rect{fill:url(http://x/y)}</style>', 'CSS links'),
    ('<foreignObject><div/></foreignObject>', 'foreignObject'),
])
def test_drawings_must_be_self_contained(tmp_path, body, reason):
    p = tmp_path / 's.svg'
    p.write_text(f'<svg xmlns="http://www.w3.org/2000/svg">{body}</svg>', encoding='utf-8')
    with pytest.raises(EditError, match=reason):
        A.check_svg(p)


def test_safe_drawing_passes_check(tmp_path):
    p = tmp_path / 'ok.svg'
    p.write_text('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><defs><linearGradient id="g"/></defs>'
                 '<rect fill="url(#g)" width="5" height="5"/><use xlink:href="#g"/><image href="data:image/png;base64,AAAA"/></svg>', encoding='utf-8')
    A.check_svg(p)
    (tmp_path / 'bad.svg').write_text('<svg', encoding='utf-8')
    with pytest.raises(EditError, match='not valid SVG'):
        A.check_svg(tmp_path / 'bad.svg')


@pytest.mark.skipif(A.find_browser() is None, reason='Chrome or Edge is not installed')
def test_shots_become_frames_timed_to_the_voice_and_render_through_the_gate(client, tmp_path):
    pid = client.post('/api/projects', json={'name': 'Anim'}).json()['id']
    files = client.data_dir / 'projects' / pid / 'files'
    (files / 'art').mkdir()
    master(voice(tmp_path / 'raw.wav', 120, 12), files / 'vo.wav')
    srt(files / 'vo.srt', 12, CUES)
    for name, colour in (('s1', '#cfe8ff'), ('s2', '#ffe9b3'), ('s3', '#d8f5d0')):
        (files / 'art' / f'{name}.svg').write_text(drawing(colour, name.upper()), encoding='utf-8')
    shots = {'voiceover': 'vo.wav', 'subtitles': 'vo.srt', 'size': '1280x720', 'output': 'anim.mp4',
             'shots': [{'svg': 'art/s1.svg', 'motion': 'push'}, {'svg': 'art/s2.svg', 'anchor': 'helium', 'motion': 'pan_right'},
                       {'card': {'text': '1986', 'style': 'date'}, 'anchor': 'nineteen eighty six'},
                       {'svg': 'art/s3.svg', 'anchor': 'the end', 'motion': 'pull'}]}
    (files / 'shots.json').write_text(json.dumps(shots), encoding='utf-8')
    started = time.time()
    r = client.post(f'/api/projects/{pid}/animation/build', json={'shots': 'shots.json'})
    assert r.status_code == 200, r.text
    built = r.json()
    assert built['drawings'] == 3 and built['edit'] == 'shots.edit.json' and time.time() - started < 60
    assert [t['start'] for t in built['timeline']] == pytest.approx([0, 4.2, 6.75, 9.0], abs=0.01)
    for name in ('s1', 's2', 's3'):
        assert gates.png_size(files / 'frames' / 'shots' / f'{name}.png') == (1280, 720)
    edit = json.loads((files / 'shots.edit.json').read_text())
    assert [v.get('file') for v in edit['visuals']] == ['frames/shots/s1.png', 'frames/shots/s2.png', None, 'frames/shots/s3.png']
    assert sum(v['seconds'] for v in edit['visuals']) == pytest.approx(12, abs=0.01)
    job = client.post(f'/api/projects/{pid}/edit/render', json={'edit': 'shots.edit.json', 'approve': 'once'}).json()
    for _ in range(600):
        job = client.get(f"/api/projects/{pid}/edit/jobs/{job['id']}").json()
        if job['status'] != 'rendering':
            break
        time.sleep(0.3)
    assert job['status'] == 'done' and job['gate']['passed'], job
    # the frame at 5 s is the second drawing (pale yellow), not the first
    def colour(at):
        return tuple(subprocess.run([FF, '-v', 'error', '-ss', str(at), '-i', str(files / 'anim.mp4'), '-frames:v', '1', '-vf', 'scale=1:1',
                                     '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout[:3])
    r1, g1, b1 = colour(2)                      # pale blue drawing
    r2, g2, b2 = colour(5)                      # pale yellow drawing
    assert b1 > r1 and r2 > b2 + 20, (colour(2), colour(5))
    # errors name the problem shot
    shots['shots'][1]['anchor'] = 'oxygen'
    (files / 'shots.json').write_text(json.dumps(shots), encoding='utf-8')
    assert 'oxygen' in client.post(f'/api/projects/{pid}/animation/build', json={'shots': 'shots.json'}).json()['detail']
    shots['shots'][1] = {'svg': 'vo.srt', 'anchor': 'helium'}
    (files / 'shots.json').write_text(json.dumps(shots), encoding='utf-8')
    assert 'not an .svg' in client.post(f'/api/projects/{pid}/animation/build', json={'shots': 'shots.json'}).json()['detail']


def test_build_animation_is_allowed_without_a_card(tmp_path):
    assert decide('build_animation', {}, tmp_path)[0] == 'allow'
