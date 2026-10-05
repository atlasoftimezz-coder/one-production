"""Stand-in DaVinci Resolve for the real executor (app/resolve_runner.py).

Runs the app's own executor against an in-memory Resolve whose project library is kept in
FAKE_RESOLVE_STATE (JSON), so tests see what a run created without any real Resolve.
FAKE_RESOLVE_VERSION   version string (default 21.0.0.47)
FAKE_RESOLVE_DOWN=1    Resolve does not answer
FAKE_RESOLVE_FAIL      'import' / 'append' / 'marker' / 'save' make that call fail
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from app import resolve_runner  # noqa: E402

def fail(what):
    return os.environ.get('FAKE_RESOLVE_FAIL', '') == what


def load():
    STATE = Path(os.environ.get('FAKE_RESOLVE_STATE', 'fake-resolve.json'))
    if STATE.is_file():
        return json.loads(STATE.read_text(encoding='utf-8'))
    return {'folders': {'': ['Owner project']}, 'current': 'Owner project', 'projects': {}, 'calls': 0}


class Item:
    def __init__(self, path):
        self.path = path


class Timeline:
    def __init__(self, name):
        self.name, self.items, self.markers = name, [], {}

    def AddMarker(self, frame, color, name, note, duration):
        if fail('marker') or frame in self.markers:
            return False
        self.markers[float(frame)] = {'color': color, 'name': name, 'note': note, 'duration': duration}
        return True

    def GetMarkers(self):
        return self.markers

    def GetTrackCount(self, kind):
        return 1 if any(kind_of(i) == kind for i in self.items) else 0

    def GetItemListInTrack(self, kind, index):
        return [i for i in self.items if kind_of(i) == kind]


def kind_of(item):
    return 'audio' if Path(item.path).suffix.lower() in ('.wav', '.mp3', '.m4a', '.aac', '.flac') else 'video'


class Project:
    def __init__(self, name, record):
        self.name, self.record, self.timelines, self.current, self.settings = name, record, [], None, {}

    def GetName(self):
        return self.name

    def SetSetting(self, key, value):
        self.settings[key] = value
        return True

    def GetSetting(self, key):
        return self.settings.get(key, '')

    def GetMediaPool(self):
        return Pool(self)

    def SetCurrentTimeline(self, timeline):
        self.current = timeline
        return True

    def GetCurrentTimeline(self):
        return self.current

    def GetTimelineCount(self):
        return len(self.timelines)


class Pool:
    def __init__(self, project):
        self.project = project

    def ImportMedia(self, paths):
        if fail('import'):
            return []
        missing = [p for p in paths if not Path(p).is_file()]
        return [] if missing else [Item(p) for p in paths]

    def CreateEmptyTimeline(self, name):
        if any(t.name == name for t in self.project.timelines):
            return None
        t = Timeline(name)
        self.project.timelines.append(t)
        return t

    def AppendToTimeline(self, items):
        placed = items[:-1] if fail('append') else items
        self.project.current.items.extend(placed)
        return list(placed)


class Manager:
    def __init__(self, state):
        self.state, self.folder, self.project = state, '', None

    def GetCurrentProject(self):
        return self.project or (Project(self.state['current'], None) if self.state['current'] else None)

    def GotoRootFolder(self):
        self.folder = ''
        return True

    def GetFolderListInCurrentFolder(self):
        return [f for f in self.state['folders'] if f and self.folder == '']

    def CreateFolder(self, name):
        self.state['folders'].setdefault(name, [])
        return True

    def OpenFolder(self, name):
        if name not in self.state['folders']:
            return False
        self.folder = name
        return True

    def GetProjectListInCurrentFolder(self):
        return list(self.state['folders'][self.folder])

    def CreateProject(self, name):
        if name in self.state['folders'][self.folder]:
            return None
        self.state['folders'][self.folder].append(name)
        self.state['current'] = name
        self.project = Project(name, self.state)
        return self.project

    def SaveProject(self):
        if fail('save') or not self.project:
            return False
        p = self.project
        self.state['projects'][p.name] = {
            'folder': self.folder, 'settings': p.settings, 'timelines': [t.name for t in p.timelines],
            'items': [Path(i.path).name for t in p.timelines for i in t.items],
            'markers': {t.name: list(t.markers.values()) for t in p.timelines}}
        return True


class FakeResolve:
    def __init__(self, state):
        self.manager = Manager(state)

    def GetProjectManager(self):
        return self.manager

    def GetProductName(self):
        return 'DaVinci Resolve Studio'

    def GetVersionString(self):
        return os.environ.get('FAKE_RESOLVE_VERSION', '21.0.0.47')


def main():
    state = load()
    state['calls'] += 1
    if os.environ.get('FAKE_RESOLVE_DOWN') == '1':
        code = resolve_runner.main(lambda: (None, 'Resolve did not answer (stand-in).'))
    else:
        fake = FakeResolve(state)
        code = resolve_runner.main(lambda: (fake, ''))
    Path(os.environ.get('FAKE_RESOLVE_STATE', 'fake-resolve.json')).write_text(json.dumps(state), encoding='utf-8')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
