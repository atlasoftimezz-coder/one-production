"""Animation (P10, CPU): shots drawn as SVG by the worker -> PNG frames -> an edit list for the editor.

shots.json (project files):
    {"voiceover": "vo.wav", "subtitles": "vo.srt", "size": "1920x1080", "fps": 30, "output": "episode.mp4",
     "shots": [{"svg": "art/s01.svg", "motion": "push"},                      # first shot starts at 0
               {"svg": "art/s02.svg", "anchor": "in nineteen eighty six"},     # starts when these words are said
               {"card": {"text": "1986", "style": "date"}, "anchor": "april"},
               {"file": "stock/pexels-video-123.mp4", "anchor": "the river", "from": 2.0}],  # stock clip / photo
     "music": {...}}

Each shot starts when its anchor words are spoken (times from the voiceover's subtitles, searched
in order) and lasts until the next shot; the last runs to the end of the voiceover. SVGs are drawn
by the AI in code (the owner's choice: no image models, no GPU) and rasterised by the Chrome or
Edge already on the PC. They are loaded as an <img>, the browser's sandbox for SVG: no scripts and
no network; external links are rejected up front anyway.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree as ET

from . import media
from .editor import CARD_STYLES, CLIPS, MOTIONS, SIZES, STILLS, EditError
from .storage import NotFound

MIN_SHOT = 0.4
BROWSERS = (
    (os.environ.get('PROGRAMFILES', r'C:\Program Files'), 'Google', 'Chrome', 'Application', 'chrome.exe'),
    (os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)'), 'Google', 'Chrome', 'Application', 'chrome.exe'),
    (os.environ.get('LOCALAPPDATA', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
    (os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)'), 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    (os.environ.get('PROGRAMFILES', r'C:\Program Files'), 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
)


def find_browser() -> str | None:
    override = os.environ.get('ONEPROD_BROWSER')
    if override and Path(override).is_file():
        return override
    for parts in BROWSERS:
        if parts[0] and Path(*parts).is_file():
            return str(Path(*parts))
    return None


def check_svg(path: Path) -> None:
    """Self-contained drawings only: no scripts, no event handlers, no links outside the file."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise EditError(f'{path.name} is not valid SVG: {exc}') from None
    if not root.tag.endswith('svg'):
        raise EditError(f'{path.name} is not an SVG drawing.')
    for el in root.iter():
        tag = el.tag.rsplit('}', 1)[-1]
        if tag in ('script', 'foreignObject'):
            raise EditError(f'{path.name}: <{tag}> is not allowed in a drawing.')
        for key, value in el.attrib.items():
            name = key.rsplit('}', 1)[-1]
            if name.startswith('on'):
                raise EditError(f'{path.name}: event handlers ({name}) are not allowed.')
            if name == 'href' and not (value.startswith('#') or value.startswith('data:image/')):
                raise EditError(f'{path.name}: links outside the drawing ({value[:60]}) are not allowed; embed images as data: URIs.')
        if tag == 'style' and re.search(r'url\(\s*["\']?(?!#|data:)', el.text or ''):
            raise EditError(f'{path.name}: CSS links outside the drawing are not allowed.')


def rasterise(browser: str, svgs: list[Path], out_dir: Path, size: str, workers: int = 4) -> list[Path]:
    """One PNG per SVG at the video size, via the browser's own screenshot (~0.5 s each)."""
    w, h = size.split('x')
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='oneprod-svg-'))
    try:
        def one(item):
            i, svg = item
            stage = work / f'{i}'
            stage.mkdir()
            shutil.copyfile(svg, stage / 'shot.svg')
            (stage / 'page.html').write_text('<!doctype html><html><body style="margin:0;background:#fff;overflow:hidden">'
                                             '<img src="shot.svg" style="display:block;width:100vw;height:100vh"></body></html>',
                                             encoding='utf-8')
            png = out_dir / (svg.stem + '.png')
            r = subprocess.run([browser, '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
                                '--disable-extensions', '--disable-background-networking', '--disable-sync',
                                f'--user-data-dir={stage / "profile"}', f'--window-size={w},{h}', f'--screenshot={png}',
                                (stage / 'page.html').as_uri()],
                               capture_output=True, text=True, errors='replace', timeout=60, creationflags=media.NO_WINDOW)
            shutil.rmtree(stage, ignore_errors=True)         # browser profiles add up on a full C: drive
            if not png.is_file():
                raise EditError(f'Drawing {svg.name} could not be rendered: {r.stderr.strip()[-200:]}')
            return png
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(one, enumerate(svgs)))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace('’', "'"))


def word_times(cues: list[dict]) -> list[tuple[str, float]]:
    """Every subtitle word with an estimated start time (spread evenly inside its cue)."""
    out = []
    for cue in cues:
        ws = words(cue['text'])
        span = max(cue['end'] - cue['start'], 0.01)
        out += [(word, cue['start'] + span * k / max(len(ws), 1)) for k, word in enumerate(ws)]
    return out


def anchor_times(anchors: list[str | None], cues: list[dict]) -> tuple[list[float | None], list[str]]:
    """Start time per shot; anchors are searched in order so a repeated phrase lands on the right one."""
    timed, missing, pos = word_times(cues), [], 0
    tokens = [w for w, _ in timed]
    out = []
    for n, anchor in enumerate(anchors):
        if n == 0:
            out.append(0.0)
            continue
        target = words(anchor or '')
        if not target:
            out.append(None)
            missing.append(f'shot {n + 1} has no anchor words')
            continue
        hit = next((i for i in range(pos, len(tokens) - len(target) + 1) if tokens[i:i + len(target)] == target), None)
        if hit is None:
            out.append(None)
            missing.append(f'shot {n + 1}: "{anchor}" is not in the subtitles after the previous shot')
            continue
        out.append(timed[hit][1])
        pos = hit + len(target)
    return out, missing


class Animation:
    def __init__(self, store, data_dir: Path):
        self.store, self.data_dir = store, data_dir

    def file_of(self, pid: str):
        def find(name: str) -> Path:
            try:
                return self.store.file_path(pid, name)
            except NotFound:
                raise EditError(f'{name} is not a file in this project.') from None
        return find

    def build(self, pid: str, shots_name: str, edit_name: str = '') -> dict:
        """Rasterise the drawings, time the shots and write the edit list. Returns a summary."""
        file_of = self.file_of(pid)
        try:
            spec = json.loads(file_of(shots_name).read_text(encoding='utf-8-sig'))
        except ValueError as exc:
            raise EditError(f'{shots_name} is not valid JSON: {exc}') from None
        shots = spec.get('shots')
        if not isinstance(shots, list) or not shots:
            raise EditError('shots must list at least one drawing or card.')
        if len(shots) > 3000:
            raise EditError('At most 3000 shots.')
        size = str(spec.get('size') or '1920x1080')
        if size not in SIZES:
            raise EditError(f'size must be one of {", ".join(sorted(SIZES))}.')
        ffmpeg = media.find_ffmpeg(self.data_dir)
        if not ffmpeg:
            raise EditError('ffmpeg was not found. Install it from Setup (free) or put it on PATH.')
        voiceover = file_of(str(spec.get('voiceover') or ''))
        seconds = media.duration(ffmpeg, voiceover)
        cues = media.srt_cues(file_of(str(spec.get('subtitles') or '')).read_text(encoding='utf-8-sig', errors='replace'))
        svgs = []
        for n, shot in enumerate(shots, 1):
            if not isinstance(shot, dict) or sum(k in shot for k in ('svg', 'card', 'file')) != 1:
                raise EditError(f'Shot {n} needs exactly one of: an svg drawing, a card, or a file (stock clip / photo).')
            if 'file' in shot:
                path = file_of(str(shot['file']))
                if path.suffix.lower() not in STILLS | CLIPS:
                    raise EditError(f'Shot {n}: {shot["file"]} is not a video clip or a picture.')
                if path.suffix.lower() in STILLS and str(shot.get('motion') or 'push') not in MOTIONS:
                    raise EditError(f'Shot {n}: motion must be one of {", ".join(sorted(MOTIONS))}.')
                try:
                    if float(shot.get('from') or 0) < 0:
                        raise ValueError
                except (TypeError, ValueError):
                    raise EditError(f'Shot {n}: "from" is the number of seconds into the clip.') from None
            elif 'svg' in shot:
                path = file_of(str(shot['svg']))
                if path.suffix.lower() != '.svg':
                    raise EditError(f'Shot {n}: {shot["svg"]} is not an .svg file.')
                check_svg(path)
                svgs.append(path)
                if str(shot.get('motion') or 'push') not in MOTIONS:
                    raise EditError(f'Shot {n}: motion must be one of {", ".join(sorted(MOTIONS))}.')
            elif str((shot['card'] or {}).get('style') or 'title') not in CARD_STYLES:
                raise EditError(f'Shot {n}: card style must be one of {", ".join(CARD_STYLES)}.')
        starts, missing = anchor_times([s.get('anchor') for s in shots], cues)
        if missing:
            raise EditError('Shots could not be timed: ' + '; '.join(missing[:8]))
        ends = starts[1:] + [seconds]
        short = [n + 1 for n, (a, b) in enumerate(zip(starts, ends)) if b - a < MIN_SHOT]
        if short:
            raise EditError(f'Shots {short[:8]} would last under {MIN_SHOT} s; move their anchors apart.')
        browser = find_browser() if svgs else None
        if svgs and not browser:
            raise EditError('Drawing needs Chrome or Edge on this PC (neither was found).')
        out_dir = self.store.files_dir(pid) / 'frames' / Path(shots_name).stem
        pngs = iter(rasterise(browser, svgs, out_dir, size) if svgs else [])
        files_root = self.store.files_dir(pid)
        visuals = []
        for shot, a, b in zip(shots, starts, ends):
            item = {'seconds': round(b - a, 3)}
            if 'svg' in shot:
                item.update(file=next(pngs).relative_to(files_root).as_posix(), motion=str(shot.get('motion') or 'push'))
            elif 'file' in shot:
                item['file'] = str(shot['file'])
                if Path(item['file']).suffix.lower() in CLIPS:
                    item['from'] = float(shot.get('from') or 0)
                else:
                    item['motion'] = str(shot.get('motion') or 'push')
            else:
                item['card'] = shot['card']
            visuals.append(item)
        edit = {k: spec[k] for k in ('fps', 'music', 'output') if k in spec}
        edit.update(voiceover=spec['voiceover'], size=size, visuals=visuals)
        if spec.get('burn_subtitles'):
            edit['subtitles'] = {'file': spec['subtitles'], 'burn': True}
        edit_name = edit_name or f'{Path(shots_name).stem}.edit.json'
        target = files_root / edit_name
        if files_root.resolve() not in target.resolve().parents:
            raise EditError('The edit list must be saved inside the project.')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(edit, indent=1), encoding='utf-8')
        lengths = [v['seconds'] for v in visuals]
        return {'edit': edit_name, 'shots': len(shots), 'drawings': len(svgs), 'frames_folder': out_dir.relative_to(files_root).as_posix(),
                'seconds': round(seconds, 3), 'median_shot': sorted(lengths)[len(lengths) // 2],
                'timeline': [{'shot': n + 1, 'start': round(a, 2), 'seconds': round(b - a, 2)} for n, (a, b) in enumerate(zip(starts, ends))][:400]}
