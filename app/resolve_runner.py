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


# ---------------------------------------------------------------- Fusion episodes (docs/P10-fusion-plan.md, F3)
EPISODE_FOLDER = 'One Production episodes'


def timecode(frame: int, fps: float) -> str:
    """Absolute timeline frame -> HH:MM:SS:FF (whole-frame rates; the episode timelines are 60 fps)."""
    f = int(round(fps))
    s, ff = divmod(int(frame), f)
    return '%02d:%02d:%02d:%02d' % (s // 3600, s // 60 % 60, s % 60, ff)


def open_episode(resolve, spec: dict):
    """Load the episode's project and timeline (fusion_stills / later steps)."""
    pm = resolve.GetProjectManager()
    current = pm.GetCurrentProject()
    if not current or current.GetName() != spec['project_name']:
        need(pm.GotoRootFolder() and pm.OpenFolder(EPISODE_FOLDER), f'The "{EPISODE_FOLDER}" folder is missing.')
        current = need(pm.LoadProject(spec['project_name']), f'Project {spec["project_name"]} could not be opened.')
    timeline = None
    for n in range(1, (current.GetTimelineCount() or 0) + 1):
        t = current.GetTimelineByIndex(n)
        if t and t.GetName() == spec.get('timeline', 'Episode'):
            timeline = t
    need(timeline, 'The episode timeline is missing.')
    need(current.SetCurrentTimeline(timeline), 'Could not open the episode timeline.')
    return pm, current, timeline


def fusion_build(resolve, spec: dict) -> dict:
    """A NEW project for the episode: 60 fps timeline, one Fusion composition per shot cut exactly at
    its frames (ripple inserts at each start, leftovers deleted), the voiceover on A1, music on A2,
    and the episode's style + each shot's brief stored in the shot's comp for the AI's scripts."""
    info = probe(resolve)
    out = {'passed': False, 'version': info['version'], 'previous_project': info['current_project'],
           'project': spec['project_name'], 'shots': [], 'changed': False}
    try:
        need(info['version'] == spec['expected_version'],
             f"Resolve is now {info['version']}, the episode was checked against {spec['expected_version']}.")
        fps = float(spec['fps'])
        shots = spec['shots']
        need(shots and shots[0]['start'] == 0, 'The first shot must start at frame 0.')
        pm = resolve.GetProjectManager()
        need(pm.GotoRootFolder(), 'Could not open the root of the project library.')
        if EPISODE_FOLDER not in (pm.GetFolderListInCurrentFolder() or []):
            need(pm.CreateFolder(EPISODE_FOLDER), f'Could not create the "{EPISODE_FOLDER}" folder.')
        need(pm.OpenFolder(EPISODE_FOLDER), f'Could not open the "{EPISODE_FOLDER}" folder.')
        need(spec['project_name'] not in (pm.GetProjectListInCurrentFolder() or []),
             'A project with this name already exists; nothing was changed.')
        project = need(pm.CreateProject(spec['project_name']), 'Resolve refused to create the project.')
        out['changed'] = True
        for key, value in (('timelineFrameRate', str(int(fps))), ('timelineResolutionWidth', str(spec['width'])),
                           ('timelineResolutionHeight', str(spec['height'])), ('timelinePlaybackFrameRate', str(int(fps)))):
            project.SetSetting(key, value)
        need(same_setting(project.GetSetting('timelineFrameRate'), str(int(fps))), 'Resolve refused the frame rate.')
        pool = project.GetMediaPool()
        media = [spec['voiceover']] + ([spec['music']] if spec.get('music') else [])
        imported = pool.ImportMedia(media) or []
        need(len(imported) == len(media), f'Imported {len(imported)} of {len(media)} audio files.')
        timeline = need(pool.CreateEmptyTimeline(spec.get('timeline', 'Episode')), 'Could not create the timeline.')
        need(project.SetCurrentTimeline(timeline), 'Could not select the timeline.')
        resolve.OpenPage('edit')
        base = timeline.GetStartFrame() or 0
        end = shots[-1]['start'] + shots[-1]['frames']
        for shot in shots:                       # ripple inserts: each one ends the shot before it
            need(timeline.SetCurrentTimecode(timecode(base + shot['start'], fps)), 'Could not move the playhead.')
            need(timeline.InsertFusionCompositionIntoTimeline(), f'Resolve refused the Fusion clip for {shot["name"]}.')
        need(timeline.SetCurrentTimecode(timecode(base + end, fps)), 'Could not move the playhead to the end.')
        timeline.InsertFusionCompositionIntoTimeline()
        leftovers = [i for i in (timeline.GetItemListInTrack('video', 1) or []) if i.GetStart() >= base + end]
        if leftovers:
            need(timeline.DeleteClips(leftovers, False), 'Could not remove the pieces past the last shot.')
        items = sorted(timeline.GetItemListInTrack('video', 1) or [], key=lambda i: i.GetStart())
        need(len(items) == len(shots), f'Expected {len(shots)} shot clips, Resolve has {len(items)}.')
        for item, shot in zip(items, shots):
            need(item.GetStart() - base == shot['start'] and item.GetDuration() == shot['frames'],
                 f'{shot["name"]} landed at {item.GetStart() - base}+{item.GetDuration()}, '
                 f'expected {shot["start"]}+{shot["frames"]}.')
            need(item.SetName(shot['name']), f'Could not name {shot["name"]}.')
            comp = need(item.GetFusionCompByIndex(1), f'{shot["name"]} has no Fusion composition.')
            comp.SetData('OneProduction.style', spec.get('style') or {})
            comp.SetData('OneProduction.shot', {k: shot.get(k) for k in ('name', 'start', 'frames', 'idea', 'anchor', 'text')}
                         | {'fps': fps})
            out['shots'].append({'name': shot['name'], 'start': item.GetStart() - base, 'frames': item.GetDuration()})
        for track, path in enumerate(media, 1):
            clip = imported[media.index(path)]
            placed = pool.AppendToTimeline([{'mediaPoolItem': clip, 'trackIndex': track, 'recordFrame': base, 'mediaType': 2}]) or []
            need(placed, f'Could not place {path} on audio track {track}.')
        need(pm.SaveProject(), 'Resolve could not save the project.')
        out['audio'] = [(i.GetStart() - base, i.GetDuration()) for n in (1, 2) for i in (timeline.GetItemListInTrack('audio', n) or [])]
        out['timeline'] = timeline.GetName()
        out['passed'] = True
    except Failed as exc:
        out['error'] = str(exc)
    except Exception as exc:                 # noqa: BLE001 - Resolve raised; report, don't crash
        out['error'] = f'Resolve raised {type(exc).__name__}: {exc}'
    return out


def fusion_stills(resolve, spec: dict) -> dict:
    """Export review stills of each shot and say which shots are still empty (nothing built yet)."""
    out = {'passed': False, 'stills': [], 'empty': [], 'shots': []}
    try:
        pm, project, timeline = open_episode(resolve, spec)
        resolve.OpenPage('edit')
        base = timeline.GetStartFrame() or 0
        fps = float(spec['fps'])
        items = {i.GetName(): i for i in (timeline.GetItemListInTrack('video', 1) or [])}
        for shot in spec['shots']:
            item = items.get(shot['name'])
            if item is None:
                out['empty'].append(shot['name'])
                continue
            comp = item.GetFusionCompByIndex(1)
            tools = len(comp.GetToolList(False) or {}) if comp else 0
            out['shots'].append({'name': shot['name'], 'tools': tools})
            if tools <= 2:                  # only MediaOut (+ MediaIn): nothing was built
                out['empty'].append(shot['name'])
                continue
            for f in shot['at']:
                f = max(0, min(int(f), item.GetDuration() - 1))
                need(timeline.SetCurrentTimecode(timecode(item.GetStart() + f, fps)), 'Could not move the playhead.')
                path = spec['out_dir'].rstrip('\\/') + '\\%s_f%04d.png' % (shot['name'], f)
                need(project.ExportCurrentFrameAsStill(path), f'Resolve could not export a still of {shot["name"]}.')
                out['stills'].append(path)
        out['passed'] = True
    except Failed as exc:
        out['error'] = str(exc)
    except Exception as exc:                 # noqa: BLE001
        out['error'] = f'Resolve raised {type(exc).__name__}: {exc}'
    return out


ACTIONS = {'execute': execute, 'fusion_build': fusion_build, 'fusion_stills': fusion_stills}


def main(connector=connect) -> int:
    request = json.loads(sys.stdin.read() or '{}')
    resolve, reason = connector()
    if resolve is None:
        answer = {'available': False, 'reason': reason}
    elif request.get('action') in ACTIONS:
        answer = ACTIONS[request['action']](resolve, request['spec'])
    else:
        answer = probe(resolve)
    sys.stdout.write(json.dumps(answer) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
