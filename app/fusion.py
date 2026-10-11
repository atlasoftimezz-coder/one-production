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

from . import fusion_kit, media
from .animation import MIN_SHOT, anchor_times
from .fusion_kit import FONTS, KIT_VERSION
from .resolve import at_least
from .storage import NotFound

FPS, WIDTH, HEIGHT = 60, 1920, 1080
MIN_VERSION = '21.0'                 # Fusion compositions on the timeline by script, as verified on 21.1
HEX = re.compile(r'^#[0-9A-Fa-f]{6}$')
MAX_SHOTS = 600
NEEDED_COLORS = ('bg', 'ink', 'accent')


class FusionError(Exception):
    pass


class ShotScripts:
    """How a visuals worker's Resolve scripts are completed and approved (owner, 2026-10-11).

    Every script gets the kit + this episode's motion.py + a guard that the episode's own project is
    open. In a DRAFT, a script runs without a card when it is a kit script whose begin("shot-NNN") calls
    all name shots of this episode; anything else still asks. The AI's own code is saved per shot so the
    owner can read what ran."""

    def __init__(self, project: str, shots: set[str], motion_file: Path | None, save_dir: Path | None, auto: bool):
        self.project, self.shots, self.motion_file, self.save_dir, self.auto_ok = project, shots, motion_file, save_dir, auto

    def motion(self) -> str | None:
        if not self.motion_file or not self.motion_file.is_file():
            return None
        try:
            return fusion_kit.check_motion(self.motion_file.read_text(encoding='utf-8-sig'))
        except ValueError:
            return None

    def expand(self, script: str) -> tuple[str, bool]:
        return fusion_kit.expand(script, self.motion(), self.project)

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
        out.append({'idea': str(s['idea'])[:600], 'anchor': str(s.get('anchor') or '')[:200] or None,
                    'text': str(s.get('text') or '')[:400]})
    return out


def frames_of(starts: list[float], seconds: float, fps: int = FPS) -> list[tuple[int, int]]:
    """Shot start seconds + voiceover length -> (start frame, frames) with no gaps or overlaps."""
    cuts = [int(round(s * fps)) for s in starts] + [int(round(seconds * fps))]
    return [(a, b - a) for a, b in zip(cuts, cuts[1:])]


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

    def plan(self, pid: str, shots_name: str, art_name: str, voiceover: str, subtitles: str, music: str = '',
             name: str = 'Episode') -> dict:
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
        out = []
        for n, (shot, (start, frames)) in enumerate(zip(shots, timed), 1):
            out.append({'name': f'shot-{n:03d}', 'start': start, 'frames': frames, **shot})
        stamp = time.strftime('%Y%m%d-%H%M%S')
        return {'project_name': f'{name[:60]} - One Production {stamp}', 'timeline': 'Episode', 'fps': FPS,
                'width': WIDTH, 'height': HEIGHT, 'voiceover': str(vo),
                'music': str(self._file(pid, music)) if music else '', 'style': art, 'shots': out,
                'kit_version': KIT_VERSION, 'seconds': round(seconds, 3)}

    def setup(self, pid: str, shots_name: str, art_name: str, voiceover: str, subtitles: str, music: str = '',
              name: str = 'Episode') -> dict:
        ok, why = self.ready()
        if not ok:
            raise FusionError(why)
        spec = self.plan(pid, shots_name, art_name, voiceover, subtitles, music, name)
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
        state.update(shots=[{k: s[k] for k in ('name', 'start', 'frames', 'idea', 'anchor', 'text')} for s in spec['shots']],
                     style=spec['style'], created=time.time(), shots_file=shots_name)
        state_name = str(Path(shots_name).with_name('fusion.json').as_posix())
        target = self.store.files_dir(pid) / state_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(state, indent=1), encoding='utf-8')
        return {'project': spec['project_name'], 'timeline': 'Episode', 'fps': FPS, 'state': state_name,
                'seconds': spec['seconds'], 'shots': [{'name': s['name'], 'start_s': round(s['start'] / FPS, 2),
                                                      'seconds': round(s['frames'] / FPS, 2), 'idea': s['idea']}
                                                     for s in spec['shots']]}

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
        shots = [{'name': s['name'], 'at': sorted({s['frames'] // 2, max(0, s['frames'] - 12)})} for s in state['shots']]
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
        return {'project': state['project_name'], 'stills': stills, 'empty': answer.get('empty') or [],
                'shots': len(state['shots']), 'folder': out_dir.relative_to(files_root).as_posix()}
