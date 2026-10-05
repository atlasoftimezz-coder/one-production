import asyncio
import json
from types import SimpleNamespace

import pytest

from app.knowledge import Knowledge
from app.learning import Learning
from app.agent.learning_tools import tools


@pytest.fixture
def memory(tmp_path):
    library = Knowledge(tmp_path / 'data')
    yield Learning(library)
    library.close()


def test_directions_persist_and_channel_styles_do_not_leak(memory):
    for p in ['a','b']:
        memory.library.set_context(p, 'Finance', '21.0')
    memory.library.set_context('c', 'Wildlife', '21.0')
    memory.save('a', 'Transitions', 'Use restrained transitions.')
    memory.save('a', 'Specific', 'Use the opening diagram.', scope='project')
    memory.save('a', 'Accessibility', 'Keep captions readable.', scope='global')
    again = Learning(memory.library)
    assert {l['title'] for l in again.recall('b')} == {'Transitions','Accessibility'}
    assert {l['title'] for l in again.recall('c')} == {'Accessibility'}
    assert 'Current user instructions take precedence' in again.instructions('a', 'transitions')


def test_corrections_preserve_history_and_forgetting_blocks_automatic_resurrection(memory):
    old = memory.save('a', 'Pace', 'Use fast transitions.')
    new = memory.save('a', 'Pace', 'Use restrained transitions.', supersedes=old['id'])
    assert memory.get('a', old['id'])['state'] == 'superseded'
    assert [l['id'] for l in memory.recall('a')] == [new['id']]
    memory.change_state('a', new['id'], 'forget')
    remembered = memory.remember_quote('a', 'Use restrained transitions.', 'Use restrained transitions.', 1)
    assert remembered['state'] == 'forgotten' and memory.recall('a') == []


def test_worker_suggestions_do_not_self_certify(memory):
    candidate = memory.save('a', 'Untested', 'A worker says this worked.', kind='candidate')
    assert memory.recall('a') == []
    with pytest.raises(ValueError, match='successful recorded execution'):
        memory.save('a', 'False success', 'Do it.', kind='recipe', evidence={'passed': False})
    memory.change_state('a', candidate['id'], 'approve')
    approved = memory.get('a', candidate['id'])
    assert approved['kind'] == 'preference' and approved['evidence']['owner_approved']
    assert memory.record_execution('a', {}, {'passed': False}) is None


def test_verified_recipes_have_run_evidence_and_version_filter(memory):
    memory.library.set_context('a', 'Finance', '21.0')
    plan = {'title': 'Assemble an explainer', 'settings': {'timelineFrameRate':'24'}, 'operations':[{'op':'append'}], 'citations':[]}
    result = {'passed':True, 'run_id':'run-1', 'plan_hash':'hash', 'version':'21.1', 'checks':{'clip_count':True}}
    lesson = memory.record_execution('a', plan, result)
    assert lesson['kind'] == 'recipe' and lesson['evidence']['run_id'] == 'run-1'
    assert memory.recall('a') == []
    assert memory.recall('a', version='21.1')[0]['id'] == lesson['id']


def test_agent_only_remembers_exact_user_direction(memory):
    store = SimpleNamespace(history=lambda pid: [{'role':'user','text':'Use restrained transitions for this channel.', 'time':1}])
    available = {tool.name:tool for tool in tools(SimpleNamespace(learning=memory,pid='a',store=store))}
    bad = json.loads(asyncio.run(available['remember_direction'].handler({'quote':'Invented preference'})))
    assert 'error' in bad and memory.recall('a') == []
    good = json.loads(asyncio.run(available['remember_direction'].handler({'quote':'Use restrained transitions'})))
    assert good['evidence']['type'] == 'user_quote'
    assert json.loads(asyncio.run(available['learning_recall'].handler({})))['lessons'][0]['id'] == good['id']


def test_learning_api_correct_forget_and_reject_recipe_forgery(client):
    pid = client.post('/api/projects',json={'name':'Memory'}).json()['id']
    base = f'/api/projects/{pid}/learning'
    first = client.post(base,json={'title':'Captions','text':'Keep captions away from faces.'}).json()
    second = client.post(base,json={'title':'Captions','text':'Keep captions below faces.','supersedes':first['id']}).json()
    assert second['supersedes'] == first['id']
    client.post(base + '/' + second['id'] + '/forget')
    assert {l['state'] for l in client.get(base).json()['lessons']} == {'forgotten','superseded'}
    other = client.post('/api/projects',json={'name':'Other'}).json()['id']
    assert client.get(f'/api/projects/{other}/learning').json()['lessons'] == []
