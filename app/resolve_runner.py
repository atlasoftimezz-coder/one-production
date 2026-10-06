"""Fixed DaVinci Resolve executor, run in its own process by app/resolve.py.

It reads one JSON request on stdin ({"action": "probe"} or {"action": "execute", "spec": ...})
and prints one JSON answer on stdout. The spec was validated by the app before it gets here and
only names operations from the table below; nothing in it is ever evaluated as code.

Standard library only: this file also runs outside the app's package (packaged Python, tests).
A separate process keeps a crash inside Resolve's scripting library away from the app.
"""
from __future__ import annotations

import json
import os
import sys

FOLDER = 'One Production (disposable)'


def connect():
    """Resolve's own scripting module; None + reason when Resolve can't be reached."""
    api = os.environ.get('RESOLVE_SCRIPT_API') or os.path.join(
        os.environ.get('PROGRAMDATA', r'C:\ProgramData'), 'Blackmagic Design', 'DaVinci Resolve', 'Support', 'Developer', 'Scripting')
    os.environ.setdefault('RESOLVE_SCRIPT_LIB', os.path.join(
        os.environ.get('PROGRAMFILES', r'C:\Program Files'), 'Blackmagic Design', 'DaVinci Resolve', 'fusionscript.dll'))
    sys.path.insert(0, os.path.join(api, 'Modules'))
    try:
        import DaVinciResolveScript as dvr  # noqa: PLC0415 - only present where Resolve is installed
    except Exception as exc:                 # noqa: BLE001 - reported to the owner
        return None, f'Resolve scripting library could not be loaded: {exc}'
    resolve = dvr.scriptapp('Resolve')
    if resolve is None:
        return None, ('Resolve did not answer. Start DaVinci Resolve Studio and set Preferences > System > '
                      'General > External scripting using to Local. The free edition has no external scripting.')
    return resolve, ''


def same_setting(read, want: str) -> bool:
    """Resolve 21.1 reads timelineFrameRate "30" back as 30.0: compare numbers as numbers."""
    try:
        return float(read) == float(want)
    except (TypeError, ValueError):
        return str(read) == str(want)


def probe(resolve) -> dict:
    pm = resolve.GetProjectManager()
    current = pm.GetCurrentProject() if pm else None
    return {'available': True, 'product': resolve.GetProductName() or '', 'version': resolve.GetVersionString() or '',
            'current_project': current.GetName() if current else ''}


class Failed(Exception):
    pass


def need(ok, message):
    if not ok:
        raise Failed(message)
    return ok


def execute(resolve, spec: dict) -> dict:
    """Apply a validated plan to a NEW project and read the result back from Resolve."""
    done: list[str] = []
    checks: dict = {}
    info = probe(resolve)
    out = {'passed': False, 'version': info['version'], 'product': info['product'],
           'previous_project': info['current_project'], 'project': spec['project_name'], 'steps': done, 'checks': checks,
           'changed': False}
    try:
        need(info['version'] == spec['expected_version'],
             f"Resolve is now {info['version']}, the plan was checked against {spec['expected_version']}.")
        pm = resolve.GetProjectManager()
        need(pm.GotoRootFolder(), 'Could not open the root of the project library.')
        if FOLDER not in (pm.GetFolderListInCurrentFolder() or []):
            need(pm.CreateFolder(FOLDER), f'Could not create the "{FOLDER}" folder.')
            out['changed'] = True
        need(pm.OpenFolder(FOLDER), f'Could not open the "{FOLDER}" folder.')
        need(spec['project_name'] not in (pm.GetProjectListInCurrentFolder() or []),
             'A project with this name already exists; nothing was changed.')
        project = need(pm.CreateProject(spec['project_name']), 'Resolve refused to create the new project.')
        out['changed'] = True
        done.append('create_project')
        for key, value in spec['settings'].items():
            need(project.SetSetting(key, value), f'Resolve refused setting {key}={value}.')
            need(same_setting(project.GetSetting(key), value), f'Setting {key} reads back as {project.GetSetting(key)!r}.')
        checks['settings'] = dict(spec['settings'])
        pool = project.GetMediaPool()
        items, timeline, appended, markers, timelines = {}, None, 0, 0, 0
        for op in spec['operations']:
            kind = op['op']
            if kind == 'import_media':
                paths = [op['paths'][f] for f in op['files']]
                created = pool.ImportMedia(paths) or []
                need(len(created) == len(paths), f'Imported {len(created)} of {len(paths)} files.')
                items.update(zip(op['files'], created))
            elif kind == 'create_timeline':
                timeline = need(pool.CreateEmptyTimeline(op['name']), f'Could not create timeline {op["name"]}.')
                need(project.SetCurrentTimeline(timeline), 'Could not select the new timeline.')
                timelines += 1
            elif kind == 'append_clips':
                need(timeline, 'Clips need a timeline first.')
                placed = pool.AppendToTimeline([items[f] for f in op['files']]) or []
                need(len(placed) == len(op['files']), f'Placed {len(placed)} of {len(op["files"])} clips.')
                appended += len(placed)
            elif kind == 'add_marker':
                need(timeline, 'Markers need a timeline first.')
                need(timeline.AddMarker(op['frame'], op['color'], op['name'], op['note'], op['duration']),
                     f'Resolve refused the marker at frame {op["frame"]}.')
                markers += 1
            else:
                raise Failed(f'Unknown operation {kind}.')            # the app never sends one
            done.append(kind)
        need(pm.SaveProject(), 'Resolve could not save the new project.')
        done.append('save_project')
        # read back what Resolve actually holds, not what the calls returned
        checks['project_name'] = project.GetName()
        checks['timelines'] = project.GetTimelineCount()
        current = project.GetCurrentTimeline()
        checks['timeline_items'] = sum(len(current.GetItemListInTrack(kind, n) or [])
                                       for kind in ('video', 'audio')
                                       for n in range(1, (current.GetTrackCount(kind) or 0) + 1)) if current else 0
        checks['markers'] = len(current.GetMarkers() or {}) if current else 0
        expected = {'project_name': spec['project_name'], 'timelines': timelines, 'markers': markers}
        for key, value in expected.items():
            need(checks[key] == value, f'Check {key}: expected {value}, Resolve has {checks[key]}.')
        need(checks['timeline_items'] >= appended, f'Expected {appended} timeline items, found {checks["timeline_items"]}.')
        checks['appended'] = appended
        out['passed'] = True
    except Failed as exc:
        out['error'] = str(exc)
    except Exception as exc:                 # noqa: BLE001 - Resolve raised; report, don't crash
        out['error'] = f'Resolve raised {type(exc).__name__}: {exc}'
    return out


def main(connector=connect) -> int:
    request = json.loads(sys.stdin.read() or '{}')
    resolve, reason = connector()
    if resolve is None:
        answer = {'available': False, 'reason': reason}
    elif request.get('action') == 'execute':
        answer = execute(resolve, request['spec'])
    else:
        answer = probe(resolve)
    sys.stdout.write(json.dumps(answer) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
