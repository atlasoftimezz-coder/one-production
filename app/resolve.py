"""Constrained DaVinci Resolve adapter (P10).

Workers may only PROPOSE a plan built from the operations in OPERATIONS; the app validates it
against the installed Resolve version and the project's own media, and stores it outside the
project folder (so an agent cannot edit it after review). Only the owner executes a plan, from
the UI, after confirming; execution always builds a NEW project in a dedicated Resolve folder and
never opens, saves or edits an existing one. No rendering: that is GPU work with its own consent.

The executor is the fixed script app/resolve_runner.py in its own process; nothing a worker
writes is ever run as code. A passed run's read-back checks become a verified recipe (learning).
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

from .knowledge import compatible, version_key
from .storage import NotFound

RUNNER = Path(__file__).with_name('resolve_runner.py')
MIN_VERSION = '18.0'
# since = first version whose scripting README/CHANGELOG makes the call reliable
OPERATIONS = {
    'import_media': {'since': '18.0', 'api': 'MediaPool.ImportMedia', 'fields': 'files: project files to bring into the media pool'},
    'create_timeline': {'since': '18.0', 'api': 'MediaPool.CreateEmptyTimeline + Project.SetCurrentTimeline', 'fields': 'name'},
    'append_clips': {'since': '20.3.2', 'api': 'MediaPool.AppendToTimeline (20.3.2 fixed a failure when no clip is selected)',
                     'fields': 'files: already imported files, appended in order to the current timeline'},
    'add_marker': {'since': '18.0', 'api': 'Timeline.AddMarker',
                   'fields': 'frame (from timeline start), color, name, note, duration (frames)'},
}
NOT_SUPPORTED = 'Rendering, deleting, opening existing projects, scripts, Fusion and color grading are not available.'
FRAME_RATES = ('23.976', '24', '25', '29.97', '30', '47.952', '48', '50', '59.94', '60')
MARKER_COLORS = ('Blue', 'Cyan', 'Green', 'Yellow', 'Red', 'Pink', 'Purple', 'Fuchsia', 'Rose', 'Lavender',
                 'Sky', 'Mint', 'Lemon', 'Sand', 'Cocoa', 'Cream')
MEDIA = {'.wav', '.mp3', '.m4a', '.aac', '.flac', '.mp4', '.mov', '.mkv', '.mxf', '.png', '.jpg', '.jpeg', '.tif', '.tiff'}
MAX_OPERATIONS, MAX_FILES = 200, 500


class PlanError(ValueError):
    pass


def at_least(installed: str, needed: str) -> bool:
    return bool(version_key(installed)) and compatible(needed, installed)


def _text(value, limit, what, required=True) -> str:
    value = re.sub(r'[\x00-\x1f]', ' ', str(value or '')).strip()[:limit]
    if required and not value:
        raise PlanError(f'{what} is required.')
    return value


def validate(plan: dict, media_of, installed: str) -> tuple[dict, list[str], dict]:
    """(normalised plan, warnings, media fingerprints). media_of(rel) -> Path inside the project or raise.
    installed '' = unknown: everything else is still checked, the version again before execution."""
    if not isinstance(plan, dict):
        raise PlanError('A plan is a JSON object.')
    extra = set(plan) - {'title', 'settings', 'operations', 'citations', 'requires_version'}
    if extra:
        raise PlanError('Unknown plan fields: ' + ', '.join(sorted(extra)) + '. ' + NOT_SUPPORTED)
    warnings: list[str] = []
    title = _text(plan.get('title'), 80, 'A title')
    requires = _text(plan.get('requires_version'), 40, 'requires_version', required=False)
    if installed:
        if not at_least(installed, MIN_VERSION):
            raise PlanError(f'Resolve {installed} is older than {MIN_VERSION}, the oldest version this adapter supports.')
        if requires and not compatible(requires, installed):
            raise PlanError(f'The plan needs Resolve {requires} but {installed} is installed. A newer manual does not make the feature available.')
    else:
        warnings.append('Installed Resolve version unknown; it is checked again before anything runs.')

    settings = {}
    for key, value in (plan.get('settings') or {}).items():
        value = str(value).strip()
        if key == 'timelineFrameRate':
            if value not in FRAME_RATES:
                raise PlanError(f'Frame rate {value} is not one of {", ".join(FRAME_RATES)}.')
        elif key in ('timelineResolutionWidth', 'timelineResolutionHeight'):
            if not value.isdigit() or not 320 <= int(value) <= 8192:
                raise PlanError(f'{key} must be a whole number of pixels from 320 to 8192.')
        else:
            raise PlanError(f'Setting {key} is not supported. Allowed: timelineFrameRate, timelineResolutionWidth, timelineResolutionHeight.')
        settings[key] = value

    ops = plan.get('operations')
    if not isinstance(ops, list) or not ops:
        raise PlanError('A plan needs at least one operation.')
    if len(ops) > MAX_OPERATIONS:
        raise PlanError(f'At most {MAX_OPERATIONS} operations per plan.')
    out, imported, media, has_timeline = [], set(), {}, False
    for n, op in enumerate(ops, 1):
        kind = op.get('op') if isinstance(op, dict) else None
        if kind not in OPERATIONS:
            raise PlanError(f'Step {n}: "{kind}" is not a supported operation. Allowed: {", ".join(OPERATIONS)}. {NOT_SUPPORTED}')
        if installed and not at_least(installed, OPERATIONS[kind]['since']):
            raise PlanError(f'Step {n}: {kind} needs Resolve {OPERATIONS[kind]["since"]} or newer; {installed} is installed.')
        allowed = {'op', 'files'} if kind in ('import_media', 'append_clips') else {'op', 'name'} if kind == 'create_timeline' \
            else {'op', 'frame', 'color', 'name', 'note', 'duration'}
        if set(op) - allowed:
            raise PlanError(f'Step {n}: unexpected fields {", ".join(sorted(set(op) - allowed))}.')
        if kind in ('import_media', 'append_clips'):
            files = op.get('files')
            if not isinstance(files, list) or not files or not all(isinstance(f, str) for f in files):
                raise PlanError(f'Step {n}: files must be a list of file names in this project.')
            clean = []
            for f in files:
                rel = f.replace('\\', '/').strip().lstrip('/')
                if kind == 'import_media':
                    if Path(rel).suffix.lower() not in MEDIA:
                        raise PlanError(f'Step {n}: {f} is not an audio, video or image file Resolve can import.')
                    try:
                        path = media_of(rel)
                    except (NotFound, OSError, ValueError):
                        raise PlanError(f'Step {n}: {f} is not a file in this project.') from None
                    st = path.stat()
                    media[rel] = {'size': st.st_size, 'mtime_ns': st.st_mtime_ns}
                    imported.add(rel)
                elif rel not in imported:
                    raise PlanError(f'Step {n}: import {f} before appending it.')
                clean.append(rel)
            if len(media) > MAX_FILES:
                raise PlanError(f'At most {MAX_FILES} media files per plan.')
            out.append({'op': kind, 'files': clean})
        elif kind == 'create_timeline':
            has_timeline = True
            out.append({'op': kind, 'name': _text(op.get('name'), 80, f'Step {n}: a timeline name')})
        else:
            if not has_timeline:
                raise PlanError(f'Step {n}: create a timeline before adding markers.')
            frame, duration = op.get('frame'), op.get('duration', 1)
            if not isinstance(frame, int) or isinstance(frame, bool) or not 0 <= frame <= 10_000_000:
                raise PlanError(f'Step {n}: frame must be a whole frame number from the timeline start.')
            if not isinstance(duration, int) or isinstance(duration, bool) or not 1 <= duration <= 100_000:
                raise PlanError(f'Step {n}: duration must be 1 or more whole frames.')
            color = str(op.get('color') or 'Blue').capitalize()
            if color not in MARKER_COLORS:
                raise PlanError(f'Step {n}: marker color must be one of {", ".join(MARKER_COLORS)}.')
            out.append({'op': kind, 'frame': frame, 'color': color, 'name': _text(op.get('name'), 80, f'Step {n}: a marker name'),
                        'note': _text(op.get('note'), 500, 'note', required=False), 'duration': duration})
        if kind == 'append_clips' and not has_timeline:
            raise PlanError(f'Step {n}: create a timeline before appending clips.')

    citations = []
    for c in (plan.get('citations') or [])[:20]:
        if isinstance(c, dict):
            citations.append({k: _text(c.get(k), 300, k, required=False) for k in ('citation', 'document', 'page')})
        else:
            citations.append({'citation': _text(c, 300, 'citation', required=False)})
    if not citations:
        warnings.append('No manual citation supports this plan.')
    return ({'title': title, 'requires_version': requires, 'settings': settings, 'operations': out,
             'citations': citations}, warnings, media)


def plan_hash(plan: dict, media: dict) -> str:
    return hashlib.sha256(json.dumps([plan, media], sort_keys=True).encode()).hexdigest()


class Resolve:
    def __init__(self, store, settings, learning, runner: list[str] | None = None, timeout: float = 300):
        self.store, self.settings, self.learning, self.timeout = store, settings, learning, timeout
        self.runner = runner
        self.lock = threading.Lock()

    # ------------------------------------------------------------ the separate process
    def command(self) -> list[str]:
        if self.runner:
            return list(self.runner)
        custom = getattr(self.settings, 'resolve_runner', None)
        if custom:
            return [sys.executable, custom] if custom.lower().endswith('.py') else [custom]
        return [sys.executable, str(RUNNER)]

    def call(self, request: dict, timeout: float) -> dict:
        try:
            proc = subprocess.run(self.command(), input=json.dumps(request), capture_output=True, text=True,
                                  encoding='utf-8', errors='replace', timeout=timeout,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            return {'available': False, 'reason': f'Resolve did not answer within {int(timeout)} s.'}
        except OSError as exc:
            return {'available': False, 'reason': f'Could not start the Resolve helper: {exc}'}
        for line in reversed(proc.stdout.splitlines()):    # Resolve's library may print before us
            try:
                answer = json.loads(line)
                if isinstance(answer, dict):
                    return answer
            except ValueError:
                continue
        return {'available': False, 'reason': 'The Resolve helper gave no answer. ' + proc.stderr.strip()[-300:]}

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, 'resolve_enabled', False))

    def probe(self) -> dict:
        """Read-only: product, version and the open project's name. Never touches Resolve while off."""
        if not self.enabled:
            return {'available': False, 'reason': 'Resolve Studio control is off.'}
        answer = self.call({'action': 'probe'}, 60)
        answer.setdefault('available', True)
        return answer

    def installed_version(self, pid: str) -> str:
        """Live Resolve if it answers, else the version the owner entered for this project/channel."""
        live = self.probe()
        if live.get('available') and live.get('version'):
            return live['version']
        return self.learning.library.context(pid)['version']

    # ------------------------------------------------------------ plans
    def folder(self, pid: str, kind: str) -> Path:
        d = self.store.project_dir(pid) / 'resolve' / kind      # outside files/: agents can't edit it
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _media_of(self, pid):
        return lambda rel: self.store.file_path(pid, rel)

    def propose(self, pid: str, plan: dict, source: str, installed: str = '') -> dict:
        clean, warnings, media = validate(plan, self._media_of(pid), installed)
        record = {'id': secrets.token_hex(8), 'status': 'proposed', 'source': source, 'created': time.time(),
                  'version_checked': installed, 'warnings': warnings, 'plan': clean, 'media': media,
                  'hash': plan_hash(clean, media), 'runs': []}
        self._write(pid, record)
        return record

    def _write(self, pid, record):
        path = self.folder(pid, 'plans') / f"{record['id']}.json"
        path.write_text(json.dumps(record, indent=1), encoding='utf-8')

    def list(self, pid: str) -> list[dict]:
        out = []
        for p in self.folder(pid, 'plans').glob('*.json'):
            try:
                out.append(json.loads(p.read_text(encoding='utf-8')))
            except ValueError:
                continue
        return sorted((r for r in out if r['status'] != 'discarded'), key=lambda r: r['created'], reverse=True)

    def get(self, pid: str, plan_id: str) -> dict:
        if not re.fullmatch(r'[0-9a-f]{16}', plan_id or ''):
            raise PlanError('Plan not found.')
        path = self.folder(pid, 'plans') / f'{plan_id}.json'
        if not path.is_file():
            raise PlanError('Plan not found.')
        return json.loads(path.read_text(encoding='utf-8'))

    def discard(self, pid: str, plan_id: str) -> dict:
        record = self.get(pid, plan_id)
        record['status'] = 'discarded'
        self._write(pid, record)
        return record

    def execute(self, pid: str, plan_id: str, expected_hash: str, confirm_saved: bool) -> dict:
        """The owner's explicit approval. Every check happens again here, against live Resolve."""
        record = self.get(pid, plan_id)
        if record['status'] not in ('proposed', 'failed'):
            raise PlanError(f"This plan is {record['status']}; propose a new one to run it again.")
        if expected_hash != record['hash']:
            raise PlanError('The plan changed after you reviewed it. Review it again.')
        if not confirm_saved:
            raise PlanError('Save your open Resolve project first and confirm it: Resolve switches to the new project.')
        if not self.enabled:
            raise PlanError('Resolve Studio control is off. Turn it on in the Resolve panel first.')
        if not self.lock.acquire(blocking=False):
            raise PlanError('Another Resolve plan is running.')
        try:
            live = self.probe()
            if not live.get('available'):
                raise PlanError(live.get('reason') or 'Resolve is not available.')
            clean, _, media = validate(record['plan'], self._media_of(pid), live['version'])
            if plan_hash(clean, media) != record['hash']:
                raise PlanError('Project media changed since the plan was reviewed. Propose it again.')
            run_id = secrets.token_hex(8)
            name = re.sub(r'[^\w \-]', '', f"OneProd {clean['title']}")[:60].strip() + time.strftime(' %Y%m%d-%H%M%S ') + run_id[:4]
            ops = [{**op, 'paths': {f: str(self.store.file_path(pid, f)) for f in op['files']}} if op['op'] == 'import_media' else op
                   for op in clean['operations']]
            spec = {'project_name': name, 'expected_version': live['version'], 'settings': clean['settings'], 'operations': ops}
            started = time.time()
            result = self.call({'action': 'execute', 'spec': spec}, self.timeout)
            if result.get('available') is False:
                result = {'passed': False, 'error': result.get('reason'), 'changed': False}
            run = {'id': run_id, 'plan': plan_id, 'started': started, 'finished': time.time(), 'version': live['version'],
                   'product': live.get('product', ''), 'project': name, 'passed': result.get('passed') is True,
                   'changed': bool(result.get('changed')), 'previous_project': result.get('previous_project', live.get('current_project', '')),
                   'steps': result.get('steps', []), 'checks': result.get('checks', {}), 'error': result.get('error', '')}
            if run['passed']:
                lesson = self.learning.record_execution(pid, clean, {'passed': True, 'run_id': run_id, 'version': live['version'],
                                                                     'plan_hash': record['hash'], 'checks': run['checks']})
                run['lesson'] = lesson['id'] if lesson else ''
            (self.folder(pid, 'runs') / f'{run_id}.json').write_text(json.dumps(run, indent=1), encoding='utf-8')
            record['status'] = 'passed' if run['passed'] else 'failed'
            record['runs'].append(run_id)
            record['last_run'] = run
            self._write(pid, record)
            return run
        finally:
            self.lock.release()
