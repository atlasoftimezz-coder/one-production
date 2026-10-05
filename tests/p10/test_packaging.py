"""P10 packaging: chapters from the script's headings timed to the voice, the upload bundle under
episode/<name>/, and the package gate."""
import json
import os

import pytest

from app import gates, packaging as P
from app.agent.policy import decide
from app.animation import find_browser
from tests.p10.media_fixtures import ff, needs_ffmpeg, png, srt

pytestmark = needs_ffmpeg
SCRIPT = """# Every element, part 1
## The lightest element
In the beginning there was hydrogen, and plenty of it.
## Noble gases
Then came helium and lithium, born in the first minutes.
## A bad year
In nineteen eighty six a reactor failed.
## The end
The end of the story, for now.
## Production notes
Not narrated: b-roll ideas.
"""
CUES = [(0, 12, 'In the beginning there was hydrogen and plenty of it'), (12, 25, 'Then came helium and lithium born in the first minutes'),
        (25, 38, 'In nineteen eighty six a reactor failed'), (38, 52, 'The end of the story for now')]


def cues(spec=CUES):
    return [{'start': a, 'end': b, 'text': t} for a, b, t in spec]


def test_chapters_follow_the_script_headings_and_merge_short_ones():
    assert P.chapters_from(SCRIPT, cues(), 60) == [
        {'time': '0:00', 'title': 'The lightest element'}, {'time': '0:12', 'title': 'Noble gases'},
        {'time': '0:25', 'title': 'A bad year'}, {'time': '0:38', 'title': 'The end'}]
    short = [(0, 12, CUES[0][2]), (12, 17, CUES[1][2]), (17, 38, CUES[2][2]), (38, 52, CUES[3][2])]
    assert [c['title'] for c in P.chapters_from(SCRIPT, cues(short), 60)] == ['The lightest element', 'Noble gases', 'The end']
    assert P.chapters_from(SCRIPT, cues(), 45) == [                         # last chapter under 10 s before the end: dropped
        {'time': '0:00', 'title': 'The lightest element'}, {'time': '0:12', 'title': 'Noble gases'}, {'time': '0:25', 'title': 'A bad year'}]
    assert P.chapters_from(SCRIPT, cues(), 30) == []                        # fewer than 3: no chapters at all
    assert P.chapters_from('Just one paragraph.', cues(), 60) == []


@pytest.mark.skipif(find_browser() is None, reason='Chrome or Edge is not installed')
def test_bundle_has_everything_for_the_upload_and_passes_the_gate(client):
    pid = client.post('/api/projects', json={'name': 'Pack'}).json()['id']
    files = client.data_dir / 'projects' / pid / 'files'
    (files / 'art').mkdir()
    ff('-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=30:duration=60', '-f', 'lavfi', '-i', 'sine=frequency=220:duration=60',
       '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', files / 'episode.mp4')
    srt(files / 'vo.srt', 60, CUES)
    (files / 'script.md').write_text(SCRIPT, encoding='utf-8')
    (files / 'art' / 'thumb.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect width="1280" height="720" '
                                            'fill="#ffd400"/><text x="640" y="400" font-size="140" text-anchor="middle" font-family="Impact">H</text></svg>',
                                            encoding='utf-8')
    spec = {'name': 'ep01', 'video': 'episode.mp4', 'subtitles': 'vo.srt', 'script': 'script.md', 'thumbnail_svg': 'art/thumb.svg',
            'title': 'Every Element Explained (Part 1)', 'title_options': ['Every Element Explained', 'The Elements in 60 Seconds'],
            'description': 'From hydrogen to the reactor that failed.', 'tags': ['science', 'elements']}
    (files / 'package.json').write_text(json.dumps(spec), encoding='utf-8')
    r = client.post(f'/api/projects/{pid}/package', json={'spec': 'package.json'})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out['gate']['passed'], out['gate']['summary']
    assert out['folder'] == 'episode/ep01' and out['files'] == ['description.txt', 'metadata.json', 'package-gate.json', 'subtitles.srt',
                                                              'thumbnail.png', 'title-options.txt', 'video.mp4']
    folder = files / 'episode' / 'ep01'
    assert gates.png_size(folder / 'thumbnail.png') == (1280, 720)
    assert os.stat(folder / 'video.mp4').st_ino == os.stat(files / 'episode.mp4').st_ino   # linked, not copied
    description = (folder / 'description.txt').read_text(encoding='utf-8')
    assert description.startswith('From hydrogen') and '0:00 The lightest element\n0:12 Noble gases' in description
    meta = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
    assert meta['title'] == spec['title'] and len(meta['chapters']) == 4
    again = client.post(f'/api/projects/{pid}/package', json={'spec': 'package.json'}).json()
    assert again['folder'] == 'episode/ep01 (2)'                                          # never overwrites a bundle
    spec.update(title='x' * 120, thumbnail_svg=None, thumbnail=None)
    (files / 'bad.json').write_text(json.dumps(spec), encoding='utf-8')
    bad = client.post(f'/api/projects/{pid}/package', json={'spec': 'bad.json'}).json()
    assert not bad['gate']['passed'] and 'title length' in bad['gate']['summary']
    assert client.post(f'/api/projects/{pid}/package', json={'spec': 'nope.json'}).status_code == 400


def test_png_thumbnail_and_policy(client, tmp_path):
    assert decide('package_episode', {}, tmp_path)[0] == 'allow'
    pid = client.post('/api/projects', json={'name': 'Pack2'}).json()['id']
    files = client.data_dir / 'projects' / pid / 'files'
    ff('-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=30:duration=3', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', files / 'v.mp4')
    png(files / 't.png', 1280, 720)
    (files / 'p.json').write_text(json.dumps({'video': 'v.mp4', 'title': 'Short', 'thumbnail': 't.png'}), encoding='utf-8')
    out = client.post(f'/api/projects/{pid}/package', json={'spec': 'p.json'}).json()
    assert out['gate']['passed'] and out['chapters'] == [] and 'thumbnail.png' in out['files'], out
