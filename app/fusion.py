"""Fusion episodes (F3, docs/P10-fusion-plan.md): the art direction and shot list a worker wrote
-> a NEW Resolve project with one Fusion composition per shot at its spoken-word frames.

    art.json    {"concept": "...", "palette": {"bg", "ink", "accent", ...: "#RRGGBB"},
                 "fonts": {"heading", "body", "mono"?: installed font}, "motion": "...", "three_d": bool}
    shots.json  {"shots": [{"idea": "what the shot shows and how it moves", "text": "on-screen words"?},
                           {"anchor": "exact words from the subtitles", "idea": "..."}, ...]}

The first shot starts at 0; every other shot starts when its anchor words are spoken (searched in
order in the voiceover's subtitles, as in animation.py) and lasts until the next one. The project
is built by the fixed runner (resolve_runner.fusion_build) at 1080p 60 fps; the AI then scripts each
shot's comp through Resolve's MCP with the kit (fusion_kit.py), and fusion_stills exports review
stills. The owner renders the finished timeline in Resolve (owner's choice, 2026-10-06).
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from . import fusion_kit, media, style_probe
from .animation import MIN_SHOT, anchor_times, word_times, words
from .fusion_kit import FONTS, KIT_VERSION
from .resolve import at_least
from .storage import NotFound

FPS, WIDTH, HEIGHT = 60, 1920, 1080
MIN_VERSION = '21.0'                 # Fusion compositions on the timeline by script, as verified on 21.1
HEX = re.compile(r'^#[0-9A-Fa-f]{6}$')
MAX_SHOTS = 600
NEEDED_COLORS = ('bg', 'ink', 'accent')
STOCK_VIDEO = ('.mp4', '.mov', '.m4v')
SHOT_KEYS = ('name', 'start', 'frames', 'idea', 'anchor', 'text', 'stock', 'from')


class FusionError(Exception):
    pass


class ShotScripts:
    """How a visuals worker's Resolve scripts are completed and approved (owner, 2026-10-11).

    Every script gets the kit + this episode's motion.py + a guard that the episode's own project is
    open. In a DRAFT, a script runs without a card when it is a kit script whose begin("shot-NNN") calls
    all name shots of this episode; anything else still asks. The AI's own code is saved per shot so the
    owner can read what ran."""

    def __init__(self, project: str, shots: set[str], motion_file: Path | None, save_dir: Path | None, auto: bool,
                 root: Path | None = None, quality: str = "final"):
        self.project, self.shots, self.motion_file, self.save_dir, self.auto_ok = project, shots, motion_file, save_dir, auto
        self.root, self.quality = root, quality

    def settings(self) -> dict:
        """What the kit needs: quality, the project folder and the generated assets (assets/index.json)."""
        out: dict = {"quality": self.quality}
        if self.root:
            out["root"] = str(self.root)
            idx = self.root / "assets" / "index.json"
            try:
                data = json.loads(idx.read_text(encoding="utf-8")) if idx.is_file() else {}
            except ValueError:
                data = {}
            out["assets"] = {k: {"file": v["file"], "w": v["w"], "h": v["h"]} for k, v in data.items()
                             if isinstance(v, dict) and {"file", "w", "h"} <= set(v)}
        return out

    def motion(self) -> str | None:
        if not self.motion_file or not self.motion_file.is_file():
            return None
        try:
            return fusion_kit.check_motion(self.motion_file.read_text(encoding='utf-8-sig'))
        except ValueError:
            return None

    def expand(self, script: str) -> tuple[str, bool]:
        return fusion_kit.expand(script, self.motion(), self.project, self.settings())

    def auto(self, script: str) -> str | None:
        """The completed script when it may run without a card, else None (-> the normal card)."""
        if not self.auto_ok or not isinstance(script, str):
            return None
        if not fusion_kit.MARKER.search(script) and not fusion_kit.EP_MARKER.search(script):
            return None
        names = fusion_kit.shots_in(script)
        if not names or any(n not in self.shots for n in names):
            return None
        if fusion_kit.EP_MARKER.search(script) and self.motion() is None:
            return None                              # a broken motion.py: let the owner see it
        out, _ = self.expand(script)
        if self.save_dir:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            for n in dict.fromkeys(names):
                (self.save_dir / f'{n}.py').write_text(script, encoding='utf-8')
        return out


def check_art(art: dict) -> dict:
    """The art direction: required colors, installed fonts only, short strings."""
    if not isinstance(art, dict):
        raise FusionError('art.json must be a JSON object.')
    palette = art.get('palette')
    if not isinstance(palette, dict) or not palette:
        raise FusionError('art.json needs a "palette" of named "#RRGGBB" colors.')
    bad = [k for k, v in palette.items() if not (isinstance(k, str) and isinstance(v, str) and HEX.match(v))]
    if bad:
        raise FusionError(f'Palette colors must look like "#1A2B3C": {", ".join(map(str, bad[:5]))}.')
    missing = [k for k in NEEDED_COLORS if k not in palette]
    if missing:
        raise FusionError(f'The palette needs at least {", ".join(NEEDED_COLORS)} (missing {", ".join(missing)}).')
    fonts = art.get('fonts') or {}
    if not isinstance(fonts, dict) or not fonts.get('heading') or not fonts.get('body'):
        raise FusionError('art.json needs "fonts" with at least "heading" and "body".')
    unknown = [f for f in fonts.values() if f not in FONTS]
    if unknown:
        raise FusionError(f'Fonts must be installed on this PC: {", ".join(FONTS)} (not {", ".join(map(str, unknown))}).')
    if not str(art.get('concept') or '').strip():
        raise FusionError('art.json needs a one-paragraph "concept": why this look suits this script.')
    return {'concept': str(art['concept'])[:1200], 'palette': dict(palette), 'fonts': dict(fonts),
            'motion': str(art.get('motion') or '')[:600], 'three_d': bool(art.get('three_d'))}


def check_shots(spec: dict) -> list[dict]:
    shots = spec.get('shots') if isinstance(spec, dict) else None
    if not isinstance(shots, list) or not shots:
        raise FusionError('shots.json must list at least one shot.')
    if len(shots) > MAX_SHOTS:
        raise FusionError(f'At most {MAX_SHOTS} shots.')
    out = []
    for n, s in enumerate(shots, 1):
        if not isinstance(s, dict) or not str(s.get('idea') or '').strip():
            raise FusionError(f'Shot {n} needs an "idea": what it shows and how it moves.')
        if n > 1 and not str(s.get('anchor') or '').strip():
            raise FusionError(f'Shot {n} needs "anchor" words (only the first shot starts at 0 without one).')
        item = {'idea': str(s['idea'])[:600], 'anchor': str(s.get('anchor') or '')[:200] or None,
                'text': str(s.get('text') or '')[:400]}
        if s.get('stock'):                          # a Pexels clip under this shot's graphics (2026-10-11)
            stock = str(s['stock']).replace('\\', '/')
            if not stock.startswith('stock/') or Path(stock).suffix.lower() not in STOCK_VIDEO:
                raise FusionError(f'Shot {n}: "stock" must be a video fetched into stock/ (.mp4/.mov), not {stock[:60]}.')
            try:
                start = float(s.get('from') or 0)
            except (TypeError, ValueError):
                raise FusionError(f'Shot {n}: "from" must be seconds into the clip.') from None
            item.update(stock=stock, **{'from': max(0.0, start)})
        out.append(item)
    return out


def frames_of(starts: list[float], seconds: float, fps: int = FPS) -> list[tuple[int, int]]:
    """Shot start seconds + voiceover length -> (start frame, frames) with no gaps or overlaps."""
    cuts = [int(round(s * fps)) for s in starts] + [int(round(seconds * fps))]
    return [(a, b - a) for a, b in zip(cuts, cuts[1:])]


SFX_MAX = 300
SFX_GAIN = (-40.0, 0.0)


def check_sfx(spec) -> list[dict]:
    """sfx.json: [{"file": "sfx/...", "shot": "shot-003" | "anchor": "words", "offset_s", "gain_db"}]."""
    items = spec.get('sfx') if isinstance(spec, dict) else spec
    if not isinstance(items, list):
        raise FusionError('sfx.json must be a list (or {"sfx": [...]}).')
    if len(items) > SFX_MAX:
        raise FusionError(f'At most {SFX_MAX} sound effects.')
    out = []
    for n, s in enumerate(items, 1):
        if not isinstance(s, dict):
            raise FusionError(f'Sound effect {n} must be an object.')
        f = str(s.get('file') or '').replace('\\', '/')
        if not f.startswith('sfx/') or Path(f).suffix.lower() not in ('.mp3', '.wav'):
            raise FusionError(f'Sound effect {n}: "file" must be a file in sfx/ (from sfx_fetch).')
        if not (s.get('shot') or s.get('anchor')):
            raise FusionError(f'Sound effect {n} needs "shot" (it plays on that cut) or "anchor" (words from the subtitles).')
        try:
            offset = float(s.get('offset_s') or 0)
            gain = float(s.get('gain_db') if s.get('gain_db') is not None else -18)
        except (TypeError, ValueError):
            raise FusionError(f'Sound effect {n}: offset_s and gain_db must be numbers.') from None
        out.append({'file': f, 'shot': str(s.get('shot') or '') or None, 'anchor': str(s.get('anchor') or '') or None,
                    'offset_s': max(-5.0, min(5.0, offset)), 'gain_db': max(SFX_GAIN[0], min(SFX_GAIN[1], gain))})
    return out


def whole_source_frames(timed: list[tuple[int, int]], rates: list[float], fps: int = FPS) -> list[tuple[int, int]]:
    """A stock shot of a 30 fps clip on the 60 fps timeline needs an even frame count (25 fps: a multiple
    of 12, 24 fps: of 5), or Resolve can't fill it exactly. Such a shot's end cut moves to the nearest
    allowed frame and the next shot starts there - a few milliseconds, never a gap or overlap."""
    import math
    starts = [s for s, _ in timed] + [timed[-1][0] + timed[-1][1]]
    for i, rate in enumerate(rates):
        whole = round(rate)
        if not rate or abs(rate - whole) > 0.01 or i == len(rates) - 1:
            continue                                  # 29.97 and the like conform anyway; the last cut is the voiceover's end
        step = fps // math.gcd(fps, whole)
        frames = starts[i + 1] - starts[i]
        fixed = max(step, int(round(frames / step)) * step)
        if starts[i] + fixed < starts[i + 2]:         # never swallow the next shot
            starts[i + 1] = starts[i] + fixed
    return [(a, b - a) for a, b in zip(starts, starts[1:])]


class Fusion:
    def __init__(self, store, data_dir: Path, resolve, settings):
        self.store, self.data_dir, self.resolve, self.settings = store, data_dir, resolve, settings

    def ready(self) -> tuple[bool, str]:
        if not self.resolve.enabled:
            return False, 'Resolve control is off - turn it on in Resolve plans.'
        if not getattr(self.settings, 'resolve_mcp', False):
            return False, "The Resolve MCP switch is off - tick it in Resolve plans (the AI scripts each shot through it)."
        return True, ''

    def _file(self, pid: str, name: str) -> Path:
        try:
            return self.store.file_path(pid, name)
        except NotFound:
            raise FusionError(f'{name} is not a file in this project.') from None

    def _json(self, pid: str, name: str) -> dict:
        try:
            return json.loads(self._file(pid, name).read_text(encoding='utf-8-sig'))
        except ValueError as exc:
            raise FusionError(f'{name} is not valid JSON: {exc}') from None

    def sfx_plan(self, pid: str, sfx_name: str, ffmpeg: str, shots: list[dict], cues: list[dict], seconds: float,
                 work: Path) -> list[dict]:
        """Time every effect (on its shot's cut or at its words, plus offset) and pre-gain it to a 48 kHz wav,
        so the timeline needs no level automation."""
        timed = word_times(cues)
        tokens = [w for w, _ in timed]
        starts = {s['name']: s['start'] / FPS for s in shots}
        out_dir = work / 'sfx'
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        placed, problems = [], []
        for n, s in enumerate(check_sfx(self._json(pid, sfx_name)), 1):
            if s['shot']:
                if s['shot'] not in starts:
                    problems.append(f'sound effect {n}: there is no {s["shot"]}')
                    continue
                at = starts[s['shot']]
            else:
                target = words(s['anchor'])
                hit = next((i for i in range(len(tokens) - len(target) + 1) if target and tokens[i:i + len(target)] == target), None)
                if hit is None:
                    problems.append(f'sound effect {n}: "{s["anchor"]}" is not in the subtitles')
                    continue
                at = timed[hit][1]
            at = max(0.0, min(seconds - 0.05, at + s['offset_s']))
            src = self._file(pid, s['file'])
            dst = out_dir / f'{n:03d}.wav'
            r = media.run([ffmpeg, '-y', '-hide_banner', '-v', 'error', '-i', str(src), '-af',
                           f"volume={s['gain_db']}dB,alimiter=limit=0.89", '-ar', '48000', '-ac', '2', str(dst)], 120)
            if r.returncode:
                problems.append(f'sound effect {n}: {s["file"]} could not be read')
                continue
            placed.append({'path': str(dst), 'frame': int(round(at * FPS)), 'file': s['file']})
        if problems:
            raise FusionError('Sound effects: ' + '; '.join(problems[:8]))
        return placed

    def plan(self, pid: str, shots_name: str, art_name: str, voiceover: str, subtitles: str, music: str = '',
             name: str = 'Episode', sfx_name: str = '') -> dict:
        """Validate and time everything; the runner spec (absolute paths) - no Resolve involved."""
        art = check_art(self._json(pid, art_name))
        shots = check_shots(self._json(pid, shots_name))
        ffmpeg = media.find_ffmpeg(self.data_dir)
        if not ffmpeg:
            raise FusionError('ffmpeg was not found (it measures the voiceover).')
        vo = self._file(pid, voiceover)
        seconds = media.duration(ffmpeg, vo)
        cues = media.srt_cues(self._file(pid, subtitles).read_text(encoding='utf-8-sig', errors='replace'))
        starts, missing = anchor_times([s['anchor'] for s in shots], cues)
        if missing:
            raise FusionError('Shots could not be timed: ' + '; '.join(missing[:8]))
        timed = frames_of(starts, seconds)
        short = [n + 1 for n, (_, f) in enumerate(timed) if f < MIN_SHOT * FPS]
        if short:
            raise FusionError(f'Shots {short[:8]} would last under {MIN_SHOT} s; move their anchors apart.')
        clips = {}
        for shot in shots:
            if shot.get('stock') and shot['stock'] not in clips:
                clip = self._file(pid, shot['stock'])
                info = media.probe(ffmpeg, clip)
                vs = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), {})
                num, _, den = str(vs.get('avg_frame_rate') or '0/1').partition('/')
                rate = float(num) / float(den or 1) if float(den or 1) else 0.0
                clips[shot['stock']] = (clip, float(info['format']['duration']), rate)
        timed = whole_source_frames(timed, [clips[s['stock']][2] if s.get('stock') else 0.0 for s in shots])
        out = []
        for n, (shot, (start, frames)) in enumerate(zip(shots, timed), 1):
            if shot.get('stock'):
                clip, length, _ = clips[shot['stock']]
                have = length - shot['from']
                if have < frames / FPS - 0.02:
                    raise FusionError(f"shot-{n:03d}: {shot['stock']} has only {max(0, have):.1f} s after \"from\" "
                                      f"{shot['from']:g} s, the shot needs {frames / FPS:.1f} s - start earlier or pick a longer clip.")
                shot = {**shot, 'stock_path': str(clip)}
            out.append({'name': f'shot-{n:03d}', 'start': start, 'frames': frames, **shot})
        sfx = self.sfx_plan(pid, sfx_name, ffmpeg, out, cues, seconds,
                            (self.store.files_dir(pid) / shots_name).parent) if sfx_name else []
        stamp = time.strftime('%Y%m%d-%H%M%S')
        return {'project_name': f'{name[:60]} - One Production {stamp}', 'timeline': 'Episode', 'fps': FPS,
                'width': WIDTH, 'height': HEIGHT, 'voiceover': str(vo),
                'music': str(self._file(pid, music)) if music else '', 'style': art, 'shots': out, 'sfx': sfx,
                'kit_version': KIT_VERSION, 'seconds': round(seconds, 3)}

    def setup(self, pid: str, shots_name: str, art_name: str, voiceover: str, subtitles: str, music: str = '',
              name: str = 'Episode', sfx_name: str = '') -> dict:
        ok, why = self.ready()
        if not ok:
            raise FusionError(why)
        spec = self.plan(pid, shots_name, art_name, voiceover, subtitles, music, name, sfx_name)
        live = self.resolve.probe()
        if not live.get('available'):
            raise FusionError(live.get('reason') or 'Resolve did not answer.')
        if not at_least(live.get('version', ''), MIN_VERSION):
            raise FusionError(f'Fusion episodes need DaVinci Resolve Studio {MIN_VERSION} or newer (found {live.get("version")}).')
        spec['expected_version'] = live['version']
        answer = self.resolve.call({'action': 'fusion_build', 'spec': spec}, 600)
        if not answer.get('passed'):
            raise FusionError('Resolve: ' + (answer.get('error') or answer.get('reason') or 'the build failed'))
        state = {k: spec[k] for k in ('project_name', 'timeline', 'fps', 'width', 'height', 'seconds', 'kit_version')}
        state.update(shots=[{k: s[k] for k in SHOT_KEYS if k in s} for s in spec['shots']],
                     sfx=[{'file': s['file'], 'frame': s['frame']} for s in spec['sfx']],
                     style=spec['style'], created=time.time(), shots_file=shots_name)
        state_name = str(Path(shots_name).with_name('fusion.json').as_posix())
        target = self.store.files_dir(pid) / state_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(state, indent=1), encoding='utf-8')
        return {'project': spec['project_name'], 'timeline': 'Episode', 'fps': FPS, 'state': state_name,
                'seconds': spec['seconds'], 'shots': [{'name': s['name'], 'start_s': round(s['start'] / FPS, 2),
                                                      'seconds': round(s['frames'] / FPS, 2), 'idea': s['idea'],
                                                      **({'stock': s['stock']} if s.get('stock') else {})}
                                                     for s in spec['shots']],
                'sfx': [{'file': s['file'], 'at_s': round(s['frame'] / FPS, 2)} for s in spec['sfx']]}

    def render_start(self, pid: str, state_name: str, out_name: str) -> dict:
        """Draft: Resolve renders the episode timeline to out_name (a project file, .mp4)."""
        ok, why = self.ready()
        if not ok:
            raise FusionError(why)
        state = self._json(pid, state_name)
        target = self.store.files_dir(pid) / out_name
        target.parent.mkdir(parents=True, exist_ok=True)
        answer = self.resolve.call({'action': 'render_start', 'spec': {
            'project_name': state['project_name'], 'timeline': state.get('timeline', 'Episode'), 'fps': state['fps'],
            'width': state.get('width', WIDTH), 'height': state.get('height', HEIGHT),
            'target_dir': str(target.parent), 'name': target.stem}}, 300)
        if not answer.get('passed'):
            raise FusionError('Resolve: ' + (answer.get('error') or answer.get('reason') or 'the render did not start'))
        return {'job': answer['job'], 'file': out_name}

    def render_status(self, pid: str, state_name: str, job: dict) -> dict:
        state = self._json(pid, state_name)
        answer = self.resolve.call({'action': 'render_status', 'spec': {'project_name': state['project_name'], 'job': job['job']}}, 120)
        if not answer.get('passed'):
            return {'failed': True, 'error': answer.get('error') or answer.get('reason') or 'Resolve did not answer'}
        if answer.get('done') and not (self.store.files_dir(pid) / job['file']).is_file():
            return {'failed': True, 'error': f"Resolve says the render is complete but {job['file']} is not there"}
        return {k: answer.get(k) for k in ('done', 'failed', 'progress', 'error')}

    def stills(self, pid: str, state_name: str) -> dict:
        """Two review stills per built shot (middle and near the end) under <folder>/stills/."""
        ok, why = self.ready()
        if not ok:
            raise FusionError(why)
        state = self._json(pid, state_name)
        files_root = self.store.files_dir(pid)
        out_dir = (files_root / state_name).parent / 'stills'
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        shots = [{'name': s['name'], 'at': sorted({s['frames'] // 2, max(0, s['frames'] - 12)}), 'stock': bool(s.get('stock'))}
                 for s in state['shots']]
        answer = self.resolve.call({'action': 'fusion_stills', 'spec': {
            'project_name': state['project_name'], 'timeline': state.get('timeline', 'Episode'), 'fps': state['fps'],
            'out_dir': str(out_dir), 'shots': shots}}, 600)
        if not answer.get('passed'):
            raise FusionError('Resolve: ' + (answer.get('error') or answer.get('reason') or 'the stills failed'))
        stills = []
        for p in answer.get('stills') or []:
            try:
                stills.append(Path(p).resolve().relative_to(files_root.resolve()).as_posix())
            except ValueError:
                continue
        out = {'project': state['project_name'], 'stills': stills, 'empty': answer.get('empty') or [],
               'shots': len(state['shots']), 'folder': out_dir.relative_to(files_root).as_posix()}
        refs = sorted((out_dir.parent / 'style' / 'refs').glob('*.jpg'))
        ffmpeg = media.find_ffmpeg(self.data_dir)
        if refs and ffmpeg and stills:
            out['qa'] = self.qa(files_root, out_dir, refs, stills, ffmpeg)
        return out

    def qa(self, files_root: Path, out_dir: Path, refs: list[Path], stills: list[str], ffmpeg: str) -> dict:
        """Owner, 2026-10-12 (first draft 5/10 next to Bravo): every shot's late still next to competitor
        frames, plus measured fill / colour richness against the competitor's - "flat" shots are named."""
        late: dict[str, Path] = {}
        for rel in stills:                                # the later still of each shot (fully built)
            late[Path(rel).name.split('_f')[0]] = files_root / rel
        names = sorted(late)
        ours = style_probe.compositions(ffmpeg, [late[n] for n in names])
        target = style_probe.compositions(ffmpeg, refs)
        flat = []
        for n, c in zip(names, ours['each']):
            if target['colours'] and c['colours'] < 0.6 * target['colours']:
                flat.append(n)
            elif target['fill'] and c['fill'] < 0.5 * target['fill']:
                flat.append(n)
        sheets = style_probe.qa_sheets(ffmpeg, refs[:3], [(n, late[n]) for n in names], out_dir.parent / 'qa')
        return {'sheets': [{'sheet': s['sheet'].relative_to(files_root).as_posix(), 'shots': s['shots']} for s in sheets],
                'target': {k: target[k] for k in ('fill', 'elements', 'colours')},
                'shots': {n: c for n, c in zip(names, ours['each'])}, 'flat': flat}
