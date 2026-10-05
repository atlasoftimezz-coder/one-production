"""P10 Resolve adapter: validation, owner-only execution on a NEW project, read-back checks and
verified recipes - all against the stand-in Resolve (fakes/fake_resolve_runner.py). No test here
opens, probes or changes a real Resolve installation."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import resolve_runner
from app.agent.policy import decide
from app.agent.resolve_tools import tools
from app.resolve import PlanError, validate

FAKE = Path(__file__).parent / 'fakes' / 'fake_resolve_runner.py'
sys.path.insert(0, str(FAKE.parent))
import fake_resolve_runner  # noqa: E402


@pytest.fixture
def rc(client, tmp_path, monkeypatch):
    """client + stand-in Resolve, its library state file and a project with media."""
    monkeypatch.setenv('ONEPROD_SETTINGS_FILE', str(tmp_path / 'settings.json'))
    monkeypatch.setenv('FAKE_RESOLVE_STATE', str(tmp_path / 'resolve.json'))
    for k in ('FAKE_RESOLVE_DOWN', 'FAKE_RESOLVE_FAIL', 'FAKE_RESOLVE_VERSION'):
        monkeypatch.delenv(k, raising=False)
    client.app.state.resolve.runner = [sys.executable, str(FAKE)]
    pid = client.post('/api/projects', json={'name': 'Edit'}).json()['id']
    for name in ('vo.wav', 'broll.mp4'):
        assert client.post(f'/api/projects/{pid}/files', files={'files': (name, b'media ' + name.encode())}).status_code == 200
    client.pid, client.base = pid, f'/api/projects/{pid}/resolve/plans'
    client.state = lambda: json.loads((tmp_path / 'resolve.json').read_text()) if (tmp_path / 'resolve.json').exists() else None
    return client


PLAN = {'title': 'Rough cut', 'settings': {'timelineFrameRate': '25', 'timelineResolutionWidth': '1920', 'timelineResolutionHeight': '1080'},
        'operations': [{'op': 'import_media', 'files': ['vo.wav', 'broll.mp4']}, {'op': 'create_timeline', 'name': 'Main'},
                       {'op': 'append_clips', 'files': ['broll.mp4', 'vo.wav']},
                       {'op': 'add_marker', 'frame': 0, 'color': 'blue', 'name': 'Hook', 'note': 'first 5 s'}],
        'citations': [{'citation': 'DaVinci Resolve 21.1 Reference Manual, PDF page 120', 'page': '120'}]}


def enable(c):
    assert c.patch('/api/resolve', json={'enabled': True}).json() == {'enabled': True}


def test_owner_runs_reviewed_plan_on_a_new_project_and_it_becomes_a_verified_recipe(rc):
    enable(rc)
    status = rc.get('/api/resolve?probe=true').json()
    assert status['status']['version'] == '21.0.0.47' and status['status']['current_project'] == 'Owner project'
    plan = rc.post(rc.base, json=PLAN)
    assert plan.status_code == 200, plan.text
    plan = plan.json()
    assert plan['version_checked'] == '21.0.0.47' and plan['plan']['operations'][3]['color'] == 'Blue'
    assert rc.get(rc.base).json()['plans'][0]['id'] == plan['id']
    # no approval yet -> Resolve untouched except the read-only probes
    assert 'One Production (disposable)' not in rc.state()['folders']
    refused = rc.post(f"{rc.base}/{plan['id']}/execute", json={'plan_hash': plan['hash'], 'confirm_saved': False})
    assert refused.status_code == 400 and 'Save your open Resolve project' in refused.text
    run = rc.post(f"{rc.base}/{plan['id']}/execute", json={'plan_hash': plan['hash'], 'confirm_saved': True})
    assert run.status_code == 200, run.text
    run = run.json()
    assert run['passed'] and run['previous_project'] == 'Owner project' and run['project'].startswith('OneProd Rough cut')
    assert run['checks'] == {'settings': PLAN['settings'], 'project_name': run['project'], 'timelines': 1,
                             'timeline_items': 2, 'markers': 1, 'appended': 2}
    state = rc.state()
    assert state['folders']['One Production (disposable)'] == [run['project']]
    assert state['folders'][''] == ['Owner project']                      # the owner's project is untouched
    assert state['projects'][run['project']]['items'] == ['broll.mp4', 'vo.wav']
    # verified recipe: active, Resolve-scoped, evidence names the run, no media paths stored
    lesson = next(l for l in rc.get(f'/api/projects/{rc.pid}/learning').json()['lessons'] if l['id'] == run['lesson'])
    assert lesson['kind'] == 'recipe' and lesson['state'] == 'active' and lesson['application'] == 'resolve'
    assert lesson['version'] == '21.0.0.47' and lesson['evidence']['run_id'] == run['id'] and lesson['evidence']['passed'] is True
    assert 'vo.wav' not in lesson['text'] and 'append_clips' in lesson['text'] and 'page 120' in lesson['text']
    again = rc.post(f"{rc.base}/{plan['id']}/execute", json={'plan_hash': plan['hash'], 'confirm_saved': True})
    assert again.status_code == 400 and 'passed' in again.text


def test_failed_resolve_call_reports_and_never_creates_a_recipe(rc, monkeypatch):
    enable(rc)
    plan = rc.post(rc.base, json=PLAN).json()
    monkeypatch.setenv('FAKE_RESOLVE_FAIL', 'append')
    run = rc.post(f"{rc.base}/{plan['id']}/execute", json={'plan_hash': plan['hash'], 'confirm_saved': True}).json()
    assert not run['passed'] and run['error'] == 'Placed 1 of 2 clips.' and run['changed'] and 'lesson' not in run
    assert run['steps'] == ['create_project', 'import_media', 'create_timeline']
    assert not [l for l in rc.get(f'/api/projects/{rc.pid}/learning').json()['lessons'] if l['kind'] == 'recipe']
    assert rc.get(rc.base).json()['plans'][0]['status'] == 'failed'
    monkeypatch.delenv('FAKE_RESOLVE_FAIL')
    retry = rc.post(f"{rc.base}/{plan['id']}/execute", json={'plan_hash': plan['hash'], 'confirm_saved': True}).json()
    assert retry['passed'] and retry['project'] != run['project']        # a retry is another new project


def test_off_unavailable_tampered_or_changed_media_is_refused_before_any_change(rc, monkeypatch):
    plan = rc.post(rc.base, json=PLAN).json()
    assert plan['version_checked'] == '' and 'unknown' in plan['warnings'][0]   # off: Resolve is never contacted
    assert rc.state() is None
    go = {'plan_hash': plan['hash'], 'confirm_saved': True}
    assert 'control is off' in rc.post(f"{rc.base}/{plan['id']}/execute", json=go).text
    enable(rc)
    assert 'changed after you reviewed' in rc.post(f"{rc.base}/{plan['id']}/execute", json={**go, 'plan_hash': 'f' * 64}).text
    monkeypatch.setenv('FAKE_RESOLVE_DOWN', '1')
    assert 'did not answer' in rc.post(f"{rc.base}/{plan['id']}/execute", json=go).text
    monkeypatch.delenv('FAKE_RESOLVE_DOWN')
    (rc.data_dir / 'projects' / rc.pid / 'files' / 'vo.wav').write_bytes(b'a different, longer take')
    assert 'media changed' in rc.post(f"{rc.base}/{plan['id']}/execute", json=go).text
    monkeypatch.setenv('FAKE_RESOLVE_VERSION', '20.2.2')
    assert 'append_clips needs Resolve 20.3.2' in rc.post(f"{rc.base}/{plan['id']}/execute", json=go).text
    assert 'One Production (disposable)' not in rc.state()['folders']           # nothing was created
    assert rc.post(f"{rc.base}/{plan['id']}/discard").json()['status'] == 'discarded'
    assert rc.get(rc.base).json()['plans'] == []
    assert rc.post(f'{rc.base}/0123456789abcdef/execute', json=go).status_code == 400


def test_validation_rejects_unsupported_operations_versions_and_paths(rc, monkeypatch):
    enable(rc)
    def bad(change, version=None):
        if version:
            monkeypatch.setenv('FAKE_RESOLVE_VERSION', version)
        r = rc.post(rc.base, json={**PLAN, **change})
        monkeypatch.delenv('FAKE_RESOLVE_VERSION', raising=False)
        assert r.status_code == 400, r.text
        return r.json()['detail']
    ops = PLAN['operations']
    assert 'not a supported operation' in bad({'operations': [{'op': 'render', 'preset': 'YouTube'}]})
    assert 'not a supported operation' in bad({'operations': [{'op': 'run_script', 'code': 'import os'}]})
    assert 'Unknown plan fields: script' in bad({'script': 'print(1)'})
    assert 'not a file in this project' in bad({'operations': [{'op': 'import_media', 'files': ['../../secrets.wav']}]})
    assert 'not a file in this project' in bad({'operations': [{'op': 'import_media', 'files': ['C:/Windows/win.ini.wav']}]})
    assert 'not an audio, video or image file' in bad({'operations': [{'op': 'import_media', 'files': ['notes.txt']}]})
    assert 'import broll.mp4 before' in bad({'operations': [ops[1], {'op': 'append_clips', 'files': ['broll.mp4']}]})
    assert 'create a timeline before' in bad({'operations': [ops[0], ops[2]]})
    assert 'Setting superScale is not supported' in bad({'settings': {'superScale': '2'}})
    assert 'Frame rate 26' in bad({'settings': {'timelineFrameRate': '26'}})
    assert 'marker color' in bad({'operations': ops[:2] + [{**ops[3], 'color': 'Gold'}]})
    assert 'unexpected fields' in bad({'operations': ops[:2] + [{**ops[3], 'customData': 'x'}]})
    assert 'needs Resolve 21.1 but 21.0.0.47' in bad({'requires_version': '21.1'})
    assert 'older than 18.0' in bad({}, version='17.4.6')
    assert 'append_clips needs Resolve 20.3.2' in bad({}, version='20.2.2')
    assert rc.post(rc.base, json={**PLAN, 'requires_version': '21.0'}).status_code == 200


def test_executor_stops_if_resolve_version_changed_and_never_reuses_a_name(tmp_path, monkeypatch):
    monkeypatch.delenv('FAKE_RESOLVE_FAIL', raising=False)
    state = {'folders': {'': ['Owner project'], 'One Production (disposable)': ['Taken']}, 'current': 'Owner project', 'projects': {}, 'calls': 0}
    spec = {'project_name': 'Taken', 'expected_version': '21.0.0.47', 'settings': {}, 'operations': []}
    out = resolve_runner.execute(fake_resolve_runner.FakeResolve(state), spec)
    assert not out['passed'] and 'already exists' in out['error'] and out['steps'] == []
    out = resolve_runner.execute(fake_resolve_runner.FakeResolve(state), {**spec, 'project_name': 'New', 'expected_version': '21.1.0.1'})
    assert not out['passed'] and 'plan was checked against 21.1.0.1' in out['error'] and not out['changed']
    assert state['folders']['One Production (disposable)'] == ['Taken']


def test_worker_tools_only_propose_and_policy_allows_them(rc, tmp_path):
    studio = SimpleNamespace(resolve=rc.app.state.resolve, pid=rc.pid, knowledge=None)
    available = {t.name: t for t in tools(studio)}
    assert set(available) == {'resolve_status', 'resolve_propose_plan'}
    status = json.loads(asyncio.run(available['resolve_status'].handler({})))
    assert status['enabled'] is False and status['installed']['available'] is False and 'append_clips' in status['operations']
    result = json.loads(asyncio.run(available['resolve_propose_plan'].handler({'plan': PLAN})))
    assert result['status'] == 'waiting for the owner'
    plan = rc.get(rc.base).json()['plans'][0]
    assert plan['source'] == 'worker' and plan['status'] == 'proposed'
    # plans live outside the project's files, so an agent editing files can't alter a reviewed plan
    assert not list((rc.data_dir / 'projects' / rc.pid / 'files').rglob('*.json'))
    bad = json.loads(asyncio.run(available['resolve_propose_plan'].handler({'plan': {'title': 'x', 'operations': [{'op': 'render'}]}})))
    assert 'not a supported operation' in bad['error']
    for name in available:
        assert decide(name, {}, tmp_path)[0] == 'allow'
        assert decide('mcp__studio__' + name, {}, tmp_path)[0] == 'allow'


def test_validate_is_pure_and_idempotent(tmp_path):
    (tmp_path / 'a.wav').write_bytes(b'x')
    plan, warnings, media = validate({'title': 'T', 'operations': [{'op': 'import_media', 'files': ['a.wav']}]},
                                     lambda rel: tmp_path / rel, '')
    assert warnings[0].startswith('Installed Resolve version unknown') and 'No manual citation' in warnings[1]
    assert validate(plan, lambda rel: tmp_path / rel, '21.0')[0] == plan and list(media) == ['a.wav']
    with pytest.raises(PlanError):
        validate([], lambda rel: tmp_path / rel, '')
