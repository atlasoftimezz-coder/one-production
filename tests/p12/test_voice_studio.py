"""Real routes, provider wire formats, audio processing and shared Claude/Codex tools.

All audio is synthetic. Cloud calls use httpx transports; no account or GPU is used.
"""
import asyncio
import base64
import io
import json
import time
import wave
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import media
from app.agent import policy
from app.agent.runs import Runs
from app.agent.studio import Studio
from app.config import Settings
from app.main import create_app
from app.tools import Approvals
from app.voice_studio_audio import analyze_words, effect_guess, script_turns

TOKEN = 'voice-studio-isolated-test-token-1234'


def wav_bytes(seconds=4, frequency=140):
    rate = 16000
    t = np.arange(round(seconds * rate)) / rate
    x = .2 * sum(np.sin(2 * np.pi * frequency * i * t) / i for i in range(1, 20))
    x *= .6 + .4 * np.sin(2 * np.pi * 4 * t) ** 2
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as out:
        out.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        out.writeframes((x * 20000).astype('<i2').tobytes())
    return stream.getvalue()


@pytest.fixture
def client(tmp_path):
    if not media.find_ffmpeg():
        pytest.skip('ffmpeg is required for synthetic audio checks')
    requests = []
    local_requests = []

    def local(request):
        local_requests.append(request)
        if request.url.path == '/api/meta':
            return httpx.Response(200, json={'voices': ['local_ref.wav'], 'presets': {'clone_exact': {}}})
        if request.url.path == '/api/voices':
            return httpx.Response(200, json={'name': 'local_ref.wav', 'seconds': 10})
        if request.url.path == '/api/compare-voice':
            return httpx.Response(200, json={'speaker_cosine': .84, 'reference_window_scores': [.82, .86],
                'model': 'synthetic-test-scorer', 'status': 'needs_listening_review', 'automatically_accepted': False})
        if request.url.path == '/api/jobs':
            return httpx.Response(200, json={'id': 'local-job'})
        if request.url.path == '/api/jobs/local-job':
            return httpx.Response(200, json={'status': 'done', 'result': {'seconds': 2}, 'sections': []})
        if request.url.path == '/api/preview/local-job/wav':
            return httpx.Response(200, content=wav_bytes(2), headers={'content-type': 'audio/wav'})
        return httpx.Response(404)

    def cloud(request):
        requests.append(request)
        if request.url.path == '/v1/speech-to-text':
            return httpx.Response(200, json={'language_code': 'en', 'words': [
                {'type': 'word', 'text': 'Hello', 'start': .1, 'end': .5, 'speaker_id': 'speaker_0'},
                {'type': 'word', 'text': 'there.', 'start': .6, 'end': 1, 'speaker_id': 'speaker_0'},
                {'type': 'word', 'text': 'Radio', 'start': 1.5, 'end': 2, 'speaker_id': 'speaker_1'},
                {'type': 'word', 'text': 'reply.', 'start': 2.1, 'end': 2.5, 'speaker_id': 'speaker_1'}]})
        if request.url.path == '/v2/voices':
            return httpx.Response(200, json={'voices': [{'voice_id': 'voice-first', 'name': 'First voice'}]})
        if request.url.path == '/v1/voices/add':
            return httpx.Response(200, json={'voice_id': 'permitted-clean-clone', 'requires_verification': False})
        if request.url.path == '/v1/files/upload':
            return httpx.Response(200, json={'file': {'file_id': 1234567}, 'base_resp': {'status_code': 0}})
        if request.url.path == '/v1/voice_clone':
            return httpx.Response(200, json={'base_resp': {'status_code': 0}, 'input_sensitive': False})
        if request.url.path == '/v1/get_voice':
            return httpx.Response(200, json={'base_resp': {'status_code': 0},
                'voice_cloning': [{'voice_id': 'peter-existing', 'voice_name': 'Peter Parker'}],
                'system_voice': [{'voice_id': 'Chinese (Mandarin)_Example', 'voice_name': 'Example narrator'}]})
        if request.url.path.endswith('/with-timestamps'):
            return httpx.Response(200, json={'audio_base64': base64.b64encode(wav_bytes(2)).decode(),
                'alignment': {'characters': list('Hello there.'),
                              'character_start_times_seconds': [i / 8 for i in range(12)],
                              'character_end_times_seconds': [(i + 1) / 8 for i in range(12)]}})
        if request.url.path == '/v1/t2a_v2':
            return httpx.Response(200, json={'base_resp': {'status_code': 0}, 'data': {'audio': wav_bytes(2, 220).hex()}})
        return httpx.Response(404)

    app = create_app(Settings(data_dir=tmp_path / 'data', open_browser=False), token=TOKEN,
                     gpu_probe=lambda: None, voice_studio_transport=httpx.MockTransport(cloud),
                     voice_transport=httpx.MockTransport(local))
    with TestClient(app, base_url='http://127.0.0.1', headers={'X-OneProd-Token': TOKEN}) as c:
        c.pid = c.post('/api/projects', json={'name': 'Voice studio test'}).json()['id']
        c.base = f'/api/projects/{c.pid}/voice-studio'
        c.cloud = requests
        c.local_requests = local_requests
        c.app_instance = app
        yield c


def key(client, provider='elevenlabs'):
    result = client.put(f'/api/voice-studio/providers/{provider}', json={'api_key': 'unit-test-key-123456789'})
    assert result.status_code == 200, result.text
    assert 'unit-test-key' not in result.text


def upload(client, kind='channel', consent=True):
    result = client.post(client.base + '/references', files={'file': ('two-voices.wav', wav_bytes())},
                         data={'kind': kind, 'consent': str(consent).lower()})
    assert result.status_code == 200, result.text
    return result.json()


def wait(client, jid):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        job = client.get(client.base + '/jobs/' + jid).json()
        if job['status'] not in ('queued', 'running'):
            return job
        time.sleep(.05)
    raise AssertionError(job)


def spec():
    return {'name': 'Dialogue', 'format': 'shorts', 'text': 'A: Hello there.\nB: Radio reply.',
            'speakers': [{'id': 'A', 'label': 'A', 'provider': 'elevenlabs', 'voice_id': 'voice-first', 'wpm': 160},
                         {'id': 'B', 'label': 'B', 'provider': 'minimax', 'voice_id': 'voice-second',
                          'effect': 'radio', 'wpm': 160, 'gap_ms': 400}]}


def test_dedicated_page_assets_and_api_auth(client):
    for path in ('/voiceovers', '/voiceovers.js', '/voiceovers.css'):
        result = client.get(path)
        assert result.status_code == 200
    assert 'One script. Both speakers. One finished voiceover.' in client.get('/voiceovers').text
    assert client.get('/api/voice-studio/providers', headers={'X-OneProd-Token': ''}).status_code == 401
    assert client.get('/voiceovers?token=anything').status_code == 200  # HTML contains no account data/token


def test_provider_keys_are_not_written_to_json_or_returned(client):
    key(client)
    service = client.app_instance.state.voice_studio
    client.put('/api/voice-studio/providers/elevenlabs', json={'model': 'eleven_multilingual_v2'})
    assert 'unit-test-key' not in service.config_file.read_text()
    state = client.get('/api/voice-studio/providers').json()
    assert next(p for p in state if p['id'] == 'elevenlabs')['key_set']
    voices = client.get('/api/voice-studio/voices/elevenlabs').json()
    assert voices['voices'][0]['id'] == 'voice-first'
    client.put('/api/voice-studio/providers/elevenlabs', json={'remove': True})
    assert not service.key('elevenlabs')


def test_upload_does_not_call_a_paid_api_and_competitors_cannot_be_cloned(client):
    ref = upload(client, 'competitor', consent=True)
    assert client.cloud == [] and ref['consent'] is False
    result = client.post(client.base + f"/references/{ref['id']}/clone/speaker_0", json={'consent': True})
    assert result.status_code == 400 and 'delivery references' in result.text


def test_analysis_requires_approval_and_measures_speaker_timing(client):
    key(client)
    ref = upload(client)
    payload = {'reference_id': ref['id'], 'expected_speakers': 2}
    assert client.post(client.base + '/analyze', json=payload).status_code == 409
    assert not client.cloud
    started = client.post(client.base + '/analyze', json={**payload, 'approve': True})
    assert started.status_code == 200, started.text
    done = wait(client, started.json()['id'])
    assert done['status'] == 'done', done
    profile = done['result']
    assert len(profile['speakers']) == 2
    assert profile['words'] == 4 and profile['pauses']['turn_gap_ms'] == 500
    assert 'not real-person identification' in profile['notes'][0]
    request = client.cloud[0]
    body = request.content
    assert b'scribe_v2' in body and b'name="diarize"' in body and b'true' in body
    updated = client.patch(client.base + f"/references/{ref['id']}", json={'speakers': [
        {'id': 'speaker_1', 'label': 'Radio host', 'effect': 'radio', 'wpm': 170}]})
    assert updated.status_code == 200, updated.text
    assert updated.json()['analysis']['speakers'][1]['effect'] == 'radio'


def test_generation_plan_is_free_and_rejects_unknown_speaker_labels(client):
    result = client.post(client.base + '/plan', json=spec())
    assert result.status_code == 200 and result.json()['requests'] == 2
    assert client.app_instance.state.voice_studio.last_reviewed_plan(client.pid)['script_text'] == spec()['text']
    assert not client.cloud
    assert client.post(client.base + '/generate', json=spec()).status_code == 409
    assert not client.cloud
    bad = {**spec(), 'text': 'Somebody: not a configured speaker'}
    assert client.post(client.base + '/plan', json=bad).status_code == 400


@pytest.mark.parametrize('names', [('Miles:', 'Peter:'), ('mIlEs:', 'PETER:'), ('[Miles]', '[Peter]'), ('B:', 'A:')])
def test_speaker_labels_route_but_are_never_spoken(names):
    speakers = [{'id': 'A', 'label': 'Peter'}, {'id': 'B', 'label': 'Miles'}]
    turns = script_turns(f'{names[0]} Pete, are you there?\n{names[1]} Yes, Miles.\nStill here.', speakers)
    assert [(t['speaker'], t['text']) for t in turns] == [
        ('B', 'Pete, are you there?'), ('A', 'Yes, Miles. Still here.')]
    # Names inside the dialogue stay spoken; only routing prefixes are removed.
    assert 'Miles.' in turns[1]['text']


def test_assistant_reuses_reviewed_dialogue_and_provider_requests_exclude_labels(client):
    service = client.app_instance.state.voice_studio
    studio = Studio(client.pid, service.store, service.service, service.legacy_jobs, lambda _: None, voice_studio=service)
    tools = {t.name: t for t in studio.tools()}
    with pytest.raises(ValueError, match='click Review'):
        service.plan(client.pid, {'use_reviewed_plan': True})
    original = spec()
    original['speakers'][0]['label'] = 'Peter'
    original['speakers'][1]['label'] = 'Miles'
    original.update(text='Miles: First radio reply.\nPeter: Second clean reply.', episode_number='23', preview=False)
    saved = client.post(client.base + '/plan', json=original).json()
    reused = asyncio.run(tools['voiceover_studio_plan'].handler({'use_reviewed_plan': True}))
    assert json.loads(reused)['turns'] == saved['turns']
    with pytest.raises(ValueError, match='on its own'):
        service.plan(client.pid, {'use_reviewed_plan': True, 'preview': True})
    with pytest.raises(ValueError, match='Approve'):
        service.start_generation(client.pid, {'use_reviewed_plan': True})
    key(client)
    key(client, 'minimax')
    # Start on the app's event loop, as the real agent handler does.
    started = client.portal.call(tools['voiceover_studio_generate'].handler, {'use_reviewed_plan': True})
    done = wait(client, json.loads(started)['id'])
    assert done['status'] == 'done', done
    assert done['plan']['episode_number'] == '23' and done['plan']['preview'] is False
    synthesis = [r for r in client.cloud if r.url.path == '/v1/t2a_v2' or r.url.path.endswith('/with-timestamps')]
    assert [json.loads(r.content)['text'] for r in synthesis] == ['First radio reply.', 'Second clean reply.']
    assert synthesis[0].url.path == '/v1/t2a_v2'
    assert synthesis[1].url.path == '/v1/text-to-speech/voice-first/with-timestamps'


def test_two_providers_effects_export_and_existing_episode_reuse(client):
    key(client)
    key(client, 'minimax')
    result = client.post(client.base + '/generate', json={**spec(), 'approve': True})
    assert result.status_code == 200, result.text
    done = wait(client, result.json()['id'])
    assert done['status'] == 'done', done
    assert {Path(f).suffix for f in done['files']} == {'.wav', '.mp3', '.srt', '.json'}
    wav_name = next(f for f in done['files'] if f.endswith('.wav'))
    wave_result = client.get(f'/api/projects/{client.pid}/files/{wav_name}')
    assert wave_result.content[:4] == b'RIFF'
    srt_name = next(f for f in done['files'] if f.endswith('.srt'))
    srt = client.get(f'/api/projects/{client.pid}/files/{srt_name}').text
    assert 'Hello there.' in srt and 'Radio reply.' in srt
    legacy = client.get('/api/voicejobs/' + done['voice_job_id']).json()
    assert legacy['status'] == 'done' and legacy['origin'] == 'voice_studio'
    assert legacy['read_check_verified'] is False
    paths = [r.url.path for r in client.cloud]
    assert '/v1/text-to-speech/voice-first/with-timestamps' in paths and '/v1/t2a_v2' in paths
    mini = json.loads(next(r for r in client.cloud if r.url.path == '/v1/t2a_v2').content)
    assert mini['voice_setting']['voice_id'] == 'voice-second'
    assert any('estimated timing' in f for f in done['flags'])


def test_passage_repair_only_bills_selected_sections_and_keeps_original(client):
    key(client)
    key(client, 'minimax')
    body = {**spec(), 'text': 'A: First passage.\nB: Second passage.\nA: Last passage.',
            'episode_number': '24', 'approve': True}
    started = client.post(client.base + '/generate', json=body).json()
    original = wait(client, started['id'])
    assert original['status'] == 'done', original
    before = len(client.cloud)
    url = client.base + f"/jobs/{original['id']}/repair"
    assert client.post(url, json={'sections': [2]}).status_code == 409
    assert client.post(url, json={'sections': [99], 'approve': True}).status_code == 400
    assert len(client.cloud) == before
    approved = client.post(url, json={'sections': [2], 'reason': 'Unnatural stress on reply', 'approve': True})
    assert approved.status_code == 200, approved.text
    repaired = wait(client, approved.json()['id'])
    assert repaired['status'] == 'done', repaired
    new_calls = client.cloud[before:]
    assert len(new_calls) == 1 and new_calls[0].url.path == '/v1/t2a_v2'
    assert json.loads(new_calls[0].content)['text'] == 'Second passage.'
    assert repaired['source_job_id'] == original['id'] and repaired['repaired_sections'] == [2]
    assert repaired['result']['review_status'] == 'needs_listening_review'
    assert [s['reused_audio'] for s in repaired['result']['sections']] == [True, False, True]
    assert set(original['files']).isdisjoint(repaired['files'])
    assert client.get(client.base + f"/jobs/{original['id']}").json() == original
    service = client.app_instance.state.voice_studio
    for i in (0, 2):
        assert (service.root(client.pid) / 'work' / original['id'] / f'raw-{i}.audio').read_bytes() == (
            service.root(client.pid) / 'work' / repaired['id'] / f'raw-{i}.audio').read_bytes()
    manifest = next(f for f in repaired['files'] if f.endswith('.json'))
    report = json.loads(service.store.file_path(client.pid, manifest).read_text(encoding='utf-8'))
    assert report['repair_reason'] == 'Unnatural stress on reply'
    assert report['repaired_sections'] == [2]
    # Missing source audio must fail before any provider work, not regenerate silently.
    (service.root(client.pid) / 'work' / original['id'] / 'raw-0.audio').unlink()
    failed = client.post(url, json={'sections': [2], 'approve': True})
    assert failed.status_code == 400 and 'reusable source audio' in failed.text
    assert len(client.cloud) == before + 1


def test_chatterbox_passage_repair_changes_seed_and_still_requires_listening(client):
    body = {'text': 'Peter: Local voice passage.', 'approve': True,
            'speakers': [{'id': 'A', 'label': 'Peter', 'provider': 'chatterbox', 'voice_id': 'local_ref.wav'}]}
    started = client.post(client.base + '/generate', json=body).json()
    assert wait(client, started['id'])['status'] == 'done'
    repair = client.post(client.base + f"/jobs/{started['id']}/repair", json={'sections': [1], 'approve': True}).json()
    done = wait(client, repair['id'])
    assert done['status'] == 'done', done
    submissions = [json.loads(r.content) for r in client.local_requests if r.url.path == '/api/jobs']
    assert submissions[0]['seed'] == 4242 and submissions[1]['seed'] != 4242
    assert all(s['auto_fix'] for s in submissions)
    assert done['result']['sections'][0]['transcript_check']['kind'] == 'chatterbox_transcript'
    assert done['result']['review_status'] == 'needs_listening_review'
    assert not client.cloud


def test_job_and_reference_are_scoped_to_project(client):
    ref = upload(client)
    other = client.post('/api/projects', json={'name': 'Other'}).json()['id']
    result = client.post(f'/api/projects/{other}/voice-studio/analyze', json={'reference_id': ref['id'], 'approve': True})
    assert result.status_code == 400
    assert client.get(client.base + '/jobs/../../accounts').status_code in (400, 404)


def test_provider_failure_is_not_retried_or_leaked(client):
    service = client.app_instance.state.voice_studio
    key(client)
    requests = []
    def fail(request):
        requests.append(request)
        return httpx.Response(429, json={'detail': 'unit-test-key-123456789'})
    service.transport = httpx.MockTransport(fail)
    body = spec()
    body['speakers'] = body['speakers'][:1]
    body['text'] = 'Hello there.'
    started = client.post(client.base + '/generate', json={**body, 'approve': True})
    done = wait(client, started.json()['id'])
    assert done['status'] == 'failed' and len(requests) == 1
    assert 'unit-test-key' not in done['error'] and 'No automatic retry' in done['error']


def test_preview_and_longform_chunking(client):
    body = spec()
    body.update(text='A: ' + 'word ' * 2000 + '\nB: ' + 'reply ' * 2000, format='longform')
    full = client.post(client.base + '/plan', json=body).json()
    assert len(full['turns']) > 10 and all(len(t['text']) <= 1000 for t in full['turns'])
    preview = client.post(client.base + '/plan', json={**body, 'preview': True}).json()
    assert preview['words'] <= 80 and len(preview['turns']) == 2


def test_analyzer_ignores_nonfinite_timestamps_and_marks_effect_uncertain():
    report = analyze_words([{'text': 'yes', 'start': 0, 'end': .4},
                            {'text': 'bad', 'start': float('nan'), 'end': 1}], 2)
    assert report['words'] == 1
    rate = 16000
    signal = np.sin(2 * np.pi * 1000 * np.arange(rate * 2) / rate)
    effect = effect_guess(signal)
    assert effect['suggested'] == 'radio' and effect['confidence'] == 'low'


def test_tools_are_available_to_both_agent_adapters(client):
    service = client.app_instance.state.voice_studio
    studio = Studio(client.pid, service.store, service.service, service.legacy_jobs, lambda _: None, voice_studio=service)
    names = {t.name for t in studio.tools()}
    assert {'voiceover_studio_status', 'voiceover_reference_analyze', 'voiceover_studio_plan',
            'voiceover_studio_generate', 'voiceover_studio_job', 'voiceover_provider_voices'} <= names
    for name in names:
        if name.startswith('voiceover_') and name != 'voiceover_status':
            for decorated in (name, 'mcp__studio__' + name):
                verdict, _ = policy.decide(decorated, {}, service.store.files_dir(client.pid))
                assert verdict == ('gate' if name in policy.PER_REQUEST else 'allow')


def test_paid_tools_cannot_inherit_an_always_or_session_allowance(tmp_path):
    approvals = Approvals(tmp_path)
    assert approvals.check('voice_studio_generation', 'always') is not None
    async def scenario():
        runs = Runs()
        run = runs.start('project', tmp_path)
        run.approved_jobs = ['voice_studio_generation']
        request = asyncio.create_task(runs.permit(run, 'voiceover_studio_generate', {'text': 'hi'}, gate=lambda **_: None))
        await asyncio.sleep(0)
        card = next(iter(run.asks.values()))
        assert card['per_request'] and not card['allow_session']
        runs.answer('project', card['id'], 'once')
        assert (await request)[0]
        second = asyncio.create_task(runs.permit(run, 'voiceover_studio_generate', {}, gate=lambda **_: None))
        await asyncio.sleep(0)
        assert run.asks
        runs.answer('project', next(iter(run.asks)), 'deny')
        assert not (await second)[0]
    asyncio.run(scenario())


def test_separate_minute_clean_identity_and_radio_pair_are_not_mixed(client):
    form = {'name': 'Miles', 'consent': 'true'}
    result = client.post(client.base + '/voices', data=form,
                         files={'file': ('miles-clean.wav', wav_bytes(60))})
    assert result.status_code == 200, result.text
    saved = result.json()
    assert saved['seconds'] == 60 and saved['name'] == 'Miles'
    clean = saved['clean_file']
    processed = client.post(client.base + f"/voices/{saved['id']}/processed",
                            files={'file': ('miles-radio.wav', wav_bytes(5, 1000))})
    assert processed.status_code == 200, processed.text
    pair = processed.json()
    assert pair['clean_file'] == clean and pair['processed_file'] != clean
    assert pair['effect_profile']['confidence'] == 'approximation'
    assert not client.cloud
    key(client)
    url = client.base + f"/voices/{saved['id']}/prepare"
    assert client.post(url, json={'provider': 'elevenlabs'}).status_code == 409
    start = client.post(url, json={'provider': 'elevenlabs', 'approve': True})
    done = wait(client, start.json()['id'])
    assert done['status'] == 'done', done
    clone_request = next(r for r in client.cloud if r.url.path == '/v1/voices/add')
    assert b'clean-voice.wav' in clone_request.content
    assert b'miles-radio' not in clone_request.content
    body = {'text': 'Miles: A clean voice with a radio effect.',
            'speakers': [{'id': 'B', 'label': 'Miles', 'provider': 'elevenlabs',
                          'channel_voice_id': saved['id'], 'effect': 'radio'}]}
    plan = client.post(client.base + '/plan', json=body)
    assert plan.status_code == 200, plan.text
    assert plan.json()['speakers'][0]['voice_id'] == 'permitted-clean-clone'
    assert 80 <= plan.json()['speakers'][0]['effect_settings']['highpass_hz'] <= 800


def test_minimax_clone_upload_purpose_and_no_unrequested_preview(client):
    saved = client.post(client.base + '/voices', data={'name': 'Peter', 'consent': 'true'},
                         files={'file': ('peter.wav', wav_bytes(10))}).json()
    key(client, 'minimax')
    started = client.post(client.base + f"/voices/{saved['id']}/prepare",
                           json={'provider': 'minimax', 'approve': True})
    done = wait(client, started.json()['id'])
    assert done['status'] == 'done', done
    upload_request = next(r for r in client.cloud if r.url.path == '/v1/files/upload')
    assert b'voice_clone' in upload_request.content
    request = next(r for r in client.cloud if r.url.path == '/v1/voice_clone')
    payload = json.loads(request.content)
    assert payload['file_id'] == 1234567 and 'text' not in payload
    assert '7 days' in done['result']['note']


def test_internal_pause_shaping_preserves_and_retimes_words(tmp_path):
    from app.voice_studio_audio import shape_pauses
    rate = 48000
    t = np.arange(rate) / rate
    tone = (.25 * np.sin(2 * np.pi * 300 * t) * 32767).astype('<i2')
    signal = np.concatenate([tone, np.zeros(rate, dtype='<i2'), tone])
    path = tmp_path / 'pauses.wav'
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        out.writeframes(signal.tobytes())
    alignment = [{'text': 'one', 'start': .1, 'end': .9}, {'text': 'two', 'start': 2.1, 'end': 2.9}]
    seconds = shape_pauses(path, 500, alignment)
    assert seconds == pytest.approx(2.5, abs=.02)
    assert alignment[1]['start'] == pytest.approx(1.6, abs=.02)


def test_chatterbox_uses_clean_clone_and_http_output_without_cloud_calls(client):
    voice = client.post(client.base + '/voices', data={'name': 'Peter', 'consent': 'true'},
                         files={'file': ('peter-clean.wav', wav_bytes(12))}).json()
    prepared = client.post(client.base + f"/voices/{voice['id']}/prepare",
                            json={'provider': 'chatterbox', 'approve': True})
    assert wait(client, prepared.json()['id'])['status'] == 'done'
    body = {'text': 'Peter: Hello from the local workstation.', 'approve': True,
            'speakers': [{'id': 'A', 'label': 'Peter', 'provider': 'chatterbox',
                          'channel_voice_id': voice['id'], 'effect': 'clean'}]}
    started = client.post(client.base + '/generate', json=body)
    assert started.status_code == 200, started.text
    done = wait(client, started.json()['id'])
    assert done['status'] == 'done', done
    assert not client.cloud
    assert any(r.url.path == '/api/preview/local-job/wav' for r in client.local_requests)


def test_full_dialogue_numbered_final_miles_distortion_and_stems(client):
    key(client)
    key(client, 'minimax')
    body = spec()
    body.update(episode_number='19', preview=False, consistent_pauses=True, pause_ms=180, turn_gap_ms=250)
    body['speakers'][1]['effect'] = 'miles_radio'
    body['text'] = 'A: Hello there. <#0.5#> Another thought.\nB: Radio reply.'
    start = client.post(client.base + '/generate', json={**body, 'approve': True})
    assert start.status_code == 200, start.text
    done = wait(client, start.json()['id'])
    assert done['status'] == 'done', done
    assert '#19 VO Final.wav' in done['files']
    assert '#19 VO Preprod.wav' in done['files']
    stems = [f for f in done['files'] if f.endswith('-stem.wav')]
    assert len(stems) == 2
    manifest_name = next(f for f in done['files'] if f.endswith('.json'))
    manifest = client.get(f'/api/projects/{client.pid}/files/' + manifest_name.replace('#', '%23')).json()
    preset = manifest['miles_distortion_reference']
    assert preset['highpass_hz'] == 666 and preset['lowpass_hz'] == 1650
    assert preset['level'] == 68.2 and preset['ceiling_db'] == -4.7 and preset['output_db'] == 5.4
    assert preset['dry_wet'] == 100 and preset['auto_level'] is True
    assert manifest['timeline'][0]['gap_ms'] == 500
    assert manifest['targets'] == {'lufs': -14, 'true_peak_ceiling': -1}
    assert 'mp3_loudness' in manifest
    assert abs(manifest['loudness']['lufs'] + 14) <= .5
    assert manifest['loudness']['true_peak'] <= -1
    assert abs(manifest['mp3_loudness']['lufs'] + 14) <= .5
    assert manifest['mp3_loudness']['true_peak'] <= -1
    assert client.get(f'/api/projects/{client.pid}/files/%2319%20VO%20Final.wav').status_code == 200


def test_provider_settings_reach_the_correct_api(client):
    key(client)
    body = spec()
    body['speakers'] = [body['speakers'][0]]
    body['text'] = 'Hello there.'
    body['speakers'][0].update(model='eleven_multilingual_v2', use_target_wpm=False, speed=1.1,
                                stability=.61, similarity=.82, style=.25)
    start = client.post(client.base + '/generate', json={**body, 'approve': True})
    assert wait(client, start.json()['id'])['status'] == 'done'
    request = next(r for r in client.cloud if r.url.path.endswith('with-timestamps'))
    payload = json.loads(request.content)
    assert payload['voice_settings'] == {'stability': .61, 'similarity_boost': .82, 'style': .25, 'speed': 1.1}


def test_consistent_silence_trims_edges_and_keeps_the_requested_gap(tmp_path):
    from app.voice_studio_audio import shape_pauses
    rate = 48000
    tone = (.3 * np.sin(2 * np.pi * 400 * np.arange(rate) / rate) * 32767).astype('<i2')
    signal = np.concatenate([np.zeros(rate, dtype='<i2'), tone, np.zeros(rate * 2, dtype='<i2'), tone, np.zeros(rate, dtype='<i2')])
    path = tmp_path / 'ripple.wav'
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        out.writeframes(signal.tobytes())
    words = [{'text': 'one', 'start': 1.1, 'end': 1.9}, {'text': 'two', 'start': 4.1, 'end': 4.9}]
    seconds = shape_pauses(path, 180, words, consistent=True)
    assert seconds == pytest.approx(2.18, abs=.03)
    assert words[0]['start'] == pytest.approx(.1, abs=.03)
    assert words[1]['start'] == pytest.approx(1.28, abs=.03)


def test_existing_minimax_clones_can_be_selected_without_recloning(client):
    key(client, 'minimax')
    result = client.get('/api/voice-studio/voices/minimax')
    assert result.status_code == 200, result.text
    assert result.json()['voices'][0]['name'] == 'Peter Parker'
    assert json.loads(client.cloud[0].content) == {'voice_type': 'all'}
    assert all(r.url.path == '/v1/get_voice' for r in client.cloud)
    body = spec()
    body['speakers'] = [body['speakers'][1]]
    body['speakers'][0]['voice_id'] = 'Chinese (Mandarin)_Example'
    body['text'] = 'Hello there.'
    assert client.post(client.base + '/plan', json=body).status_code == 200
