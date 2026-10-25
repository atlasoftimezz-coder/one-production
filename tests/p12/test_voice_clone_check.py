"""Synthetic clone/check flows, honest scoring and provider/permission boundaries."""
import importlib.util
import json
import threading
import types
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.agent.studio import Studio
from voice.similarity import score_embeddings
from .test_voice_studio import client, key, wait, wav_bytes


def voice(client):
    result = client.post(client.base + '/voices', data={'name': 'Miles', 'consent': 'true'},
                         files={'file': ('miles-clean.wav', wav_bytes(12))})
    assert result.status_code == 200, result.text
    return result.json()


def test_cosine_evidence_is_not_percentage_or_automatic_acceptance():
    assert score_embeddings([[1, 0]], [[1, 0]])['speaker_cosine'] == 1
    assert score_embeddings([[1, 0]], [[0, 1]])['speaker_cosine'] == 0
    assert score_embeddings([[1, 0]], [[-1, 0]])['speaker_cosine'] == -1
    result = score_embeddings([[1, 0], [.9, .1]], [[1, .1]])
    assert result['reference_windows'] == 2 and result['reference_consistency'] > .9
    assert not result['automatically_accepted'] and result['status'] == 'needs_listening_review'
    for ref, take in [([[0, 0]], [[1, 0]]), ([[1, 0]], [[np.nan, 1]]), ([[1, 0]], [[1, 0, 1]])]:
        with pytest.raises(ValueError):
            score_embeddings(ref, take)


def test_scorer_loads_trained_weights_locally_and_never_uses_random_encoder(monkeypatch):
    import sys
    from voice.similarity import cached_encoder
    calls = []
    class Encoder:
        def load_state_dict(self, weights, strict):
            assert weights == {'trained-weights': True} and strict
            calls.append('loaded')
        def to(self, device):
            assert device == 'cpu'
            return self
        def eval(self):
            return self
    def download(repo, filename, **kwargs):
        assert repo == 'ResembleAI/chatterbox' and kwargs == {'local_files_only': True}
        calls.append(filename)
        return 'already-cached-ve.safetensors'
    monkeypatch.setitem(sys.modules, 'chatterbox', types.ModuleType('chatterbox'))
    monkeypatch.setitem(sys.modules, 'chatterbox.models', types.ModuleType('chatterbox.models'))
    monkeypatch.setitem(sys.modules, 'chatterbox.models.voice_encoder', types.SimpleNamespace(VoiceEncoder=Encoder))
    monkeypatch.setitem(sys.modules, 'huggingface_hub', types.SimpleNamespace(hf_hub_download=download))
    monkeypatch.setitem(sys.modules, 'safetensors', types.ModuleType('safetensors'))
    monkeypatch.setitem(sys.modules, 'safetensors.torch', types.SimpleNamespace(load_file=lambda *a, **kw: {'trained-weights': True}))
    monkeypatch.setitem(sys.modules, 'torch', types.ModuleType('torch'))
    assert isinstance(cached_encoder(), Encoder)
    assert calls == ['ve.safetensors', 'loaded']
    def missing(*args, **kwargs):
        assert kwargs == {'local_files_only': True}
        raise FileNotFoundError()
    monkeypatch.setitem(sys.modules, 'huggingface_hub', types.SimpleNamespace(hf_hub_download=missing))
    with pytest.raises(ValueError, match='not installed locally'):
        cached_encoder()


def test_clone_check_defaults_to_local_and_requires_fresh_approval(client):
    saved = voice(client)
    client.post(client.base + f"/voices/{saved['id']}/processed", files={'file': ('radio.wav', wav_bytes(4, 1000))})
    url = client.base + f"/voices/{saved['id']}/clone-check"
    assert client.post(url, json={}).status_code == 409
    assert not client.cloud and not client.local_requests
    started = client.post(url, json={'approve': True, 'attempts': 1})
    assert started.status_code == 200, started.text
    done = wait(client, started.json()['id'])
    assert done['status'] == 'done', done
    report = done['result']
    assert report['provider'] == 'chatterbox' and report['reused_existing_clone'] is False
    assert report['reference_file'] == saved['clean_file']
    assert report['candidates'][0]['comparison']['speaker_cosine'] == .84
    assert report['recommended_take'] == 1 and report['acceptance_threshold'] is None
    assert not report['automatically_accepted'] and report['review_status'] == 'needs_listening_review'
    assert len(done['files']) == 2 and not client.cloud
    requests = client.local_requests
    assert len([r for r in requests if r.url.path == '/api/voices']) == 1
    compare = next(r for r in requests if r.url.path == '/api/compare-voice')
    assert b'clean-reference.wav' in compare.content and b'clone-comparison.wav' in compare.content
    assert b'radio.wav' not in compare.content
    # Reuse clone identity; generate another test only with another approval.
    assert client.post(url, json={}).status_code == 409
    again = client.post(url, json={'approve': True, 'attempts': 1}).json()
    assert wait(client, again['id'])['result']['reused_existing_clone']
    assert len([r for r in requests if r.url.path == '/api/voices']) == 1
    binding = client.app_instance.state.voice_studio.channel_voice(client.pid, saved['id'])['providers']['chatterbox']
    assert binding['latest_check']['job_id'] == again['id']


@pytest.mark.parametrize('provider', ['elevenlabs', 'minimax'])
def test_requested_cloud_provider_clones_and_scores_locally(client, provider):
    saved = voice(client)
    key(client, provider)
    started = client.post(client.base + f"/voices/{saved['id']}/clone-check",
                          json={'provider': provider, 'approve': True, 'attempts': 2})
    assert started.status_code == 200, started.text
    done = wait(client, started.json()['id'])
    assert done['status'] == 'done', done
    assert done['result']['provider'] == provider and len(done['result']['candidates']) == 2
    paths = [r.url.path for r in client.cloud]
    if provider == 'elevenlabs':
        assert paths.count('/v1/voices/add') == 1
        assert len([p for p in paths if p.endswith('/with-timestamps')]) == 2
        assert '/v1/voice_clone' not in paths
    else:
        assert paths.count('/v1/voice_clone') == 1 and paths.count('/v1/t2a_v2') == 2
        assert '/v1/voices/add' not in paths
    assert len([r for r in client.local_requests if r.url.path == '/api/compare-voice']) == 2
    assert not any(r.url.path == '/api/jobs' for r in client.local_requests)


def test_missing_scorer_stops_extra_takes_and_never_claims_a_match(client, monkeypatch):
    saved = voice(client)
    async def missing(*args):
        raise RuntimeError('Trained scorer unavailable')
    monkeypatch.setattr(client.app_instance.state.voice_studio.service, 'compare_voice', missing)
    started = client.post(client.base + f"/voices/{saved['id']}/clone-check", json={'attempts': 3, 'approve': True}).json()
    done = wait(client, started['id'])
    assert done['status'] == 'done', done
    assert len(done['result']['candidates']) == 1 and done['result']['recommended_take'] is None
    assert done['result']['candidates'][0]['comparison']['status'] == 'unavailable'
    assert not done['result']['automatically_accepted'] and done['flags']
    assert len([r for r in client.local_requests if r.url.path == '/api/jobs']) == 1


def test_clone_check_honors_provider_verification_without_generating(client):
    saved = voice(client)
    key(client)
    service = client.app_instance.state.voice_studio
    calls = []
    def verification(request):
        calls.append(request)
        return httpx.Response(200, json={'voice_id': 'verify-me', 'requires_verification': True})
    service.transport = httpx.MockTransport(verification)
    url = client.base + f"/voices/{saved['id']}/clone-check"
    started = client.post(url, json={'provider': 'elevenlabs', 'approve': True}).json()
    done = wait(client, started['id'])
    assert done['status'] == 'done' and done['result']['status'] == 'provider_verification_required'
    assert len(calls) == 1 and not done['files']
    assert client.post(url, json={'provider': 'elevenlabs', 'approve': True}).status_code == 400


def test_clone_check_is_project_scoped_and_bounded(client):
    saved = voice(client)
    other = client.post('/api/projects', json={'name': 'Other channel'}).json()['id']
    assert client.post(f"/api/projects/{other}/voice-studio/voices/{saved['id']}/clone-check", json={'approve': True}).status_code == 400
    url = client.base + f"/voices/{saved['id']}/clone-check"
    assert client.post(url, json={'attempts': 11, 'approve': True}).status_code == 422
    assert client.post(url, json={'target_similarity': 1.1, 'approve': True}).status_code == 422
    assert client.post(url, json={'provider': 'invented-api', 'approve': True}).status_code == 400
    assert client.post(url, json={'test_text': 'Miles: Do not speak this speaker label.', 'approve': True}).status_code == 400
    assert not client.cloud and not client.local_requests


@pytest.mark.parametrize('scores,target,limit,reason,best,takes', [
    ([.65, .84, .91, .99], .9, 10, 'identity_target_reached', 3, 3),
    ([.8, .799, .8, .99], None, 10, 'no_improvement', 1, 3),
    ([.8, .89, .85], .95, 3, 'attempt_limit', 2, 3),
])
def test_automatic_refinement_stops_and_retains_best_settings(client, monkeypatch, scores, target, limit, reason, best, takes):
    saved = voice(client)
    svc = client.app_instance.state.voice_studio
    readings = iter(scores)
    async def compare(*args):
        return {'speaker_cosine': next(readings), 'status': 'needs_listening_review'}
    monkeypatch.setattr(svc.service, 'compare_voice', compare)
    started = client.post(client.base + f"/voices/{saved['id']}/clone-check",
                          json={'attempts': limit, 'auto_retry': True, 'target_similarity': target, 'approve': True}).json()
    done = wait(client, started['id'])
    assert done['status'] == 'done', done
    report = done['result']
    assert report['stop_reason'] == reason and report['recommended_take'] == best
    assert len(report['candidates']) == takes
    assert report['identity_target_reached'] == (reason == 'identity_target_reached')
    assert report['review_status'] == 'needs_listening_review' and not report['automatically_accepted']
    requests = [json.loads(r.content) for r in client.local_requests if r.url.path == '/api/jobs']
    assert len(requests) == takes and requests[0]['cfg_weight'] != requests[2]['cfg_weight']
    binding = svc.channel_voice(client.pid, saved['id'])['providers']['chatterbox']
    winning = report['candidates'][best - 1]['generation_settings']
    assert binding['generation_settings'] == winning and binding['generation_settings_source']['provisional']
    assert len([r for r in client.local_requests if r.url.path == '/api/voices']) == 1
    # The selected parameters are used by production, and a reviewed snapshot stays frozen.
    body = {'text': 'Miles: Use the best matching cloned voice settings.', 'speakers': [
        {'id': 'B', 'label': 'Miles', 'provider': 'chatterbox', 'channel_voice_id': saved['id']}]}
    reviewed = svc.plan(client.pid, body)
    assert reviewed['speakers'][0]['chatterbox_settings']['cfg_weight'] == winning['cfg_weight']
    changed = svc.channel_voice(client.pid, saved['id'])
    changed['providers']['chatterbox']['generation_settings']['cfg_weight'] = .77
    svc.save_channel_voice(client.pid, changed)
    frozen = svc.plan(client.pid, {'use_reviewed_plan': True})
    assert frozen['speakers'][0]['chatterbox_settings'] == reviewed['speakers'][0]['chatterbox_settings']
    studio = Studio(client.pid, svc.store, svc.service, svc.legacy_jobs, lambda _: None, voice_studio=svc)
    tool = next(t for t in studio.tools() if t.name == 'voiceover_studio_generate')
    generation = json.loads(client.portal.call(tool.handler, {'use_reviewed_plan': True}))
    assert wait(client, generation['id'])['status'] == 'done'
    last = json.loads([r for r in client.local_requests if r.url.path == '/api/jobs'][-1].content)
    assert last['cfg_weight'] == winning['cfg_weight'] and last['seed'] == winning['seed']


def test_high_similarity_misread_is_not_promoted_or_treated_as_target_reached(client, monkeypatch):
    saved = voice(client)
    svc = client.app_instance.state.voice_studio
    readings = iter([.99, .85, .92])
    async def compare(*args):
        return {'speaker_cosine': next(readings)}
    monkeypatch.setattr(svc.service, 'compare_voice', compare)
    original, call_count = svc.synthesize, 0
    async def synthesize(speaker, text, destination):
        nonlocal call_count
        result = await original(speaker, text, destination)
        call_count += 1
        destination.with_suffix('.qc.json').write_text(json.dumps({'sections': [
            {'status': 'check' if call_count == 1 else 'ok'}]}), encoding='utf-8')
        return result
    monkeypatch.setattr(svc, 'synthesize', synthesize)
    started = client.post(client.base + f"/voices/{saved['id']}/clone-check",
                          json={'attempts': 5, 'target_similarity': .9, 'approve': True}).json()
    report = wait(client, started['id'])['result']
    assert report['stop_reason'] == 'identity_target_reached' and report['recommended_take'] == 3
    assert report['candidates'][0]['known_misread'] and len(report['candidates']) == 3
    assert not report['automatically_accepted']


def test_cloud_refinement_defaults_are_loaded_but_manual_controls_can_override(client, monkeypatch):
    saved = voice(client)
    key(client)
    svc = client.app_instance.state.voice_studio
    readings = iter([.7, .91])
    async def compare(*args):
        return {'speaker_cosine': next(readings)}
    monkeypatch.setattr(svc.service, 'compare_voice', compare)
    started = client.post(client.base + f"/voices/{saved['id']}/clone-check",
                          json={'provider': 'elevenlabs', 'attempts': 5, 'target_similarity': .9, 'approve': True}).json()
    report = wait(client, started['id'])['result']
    assert report['recommended_take'] == 2 and report['stop_reason'] == 'identity_target_reached'
    speaker = {'id': 'B', 'label': 'Miles', 'provider': 'elevenlabs', 'channel_voice_id': saved['id'],
               'similarity': .2, 'stability': .1, 'speed': 1.1}
    body = {'text': 'Miles: Production should use the selected clone controls.', 'speakers': [speaker]}
    planned = svc.plan(client.pid, body)['speakers'][0]
    assert planned['similarity'] == .9 and planned['stability'] == .5 and planned['speed'] == 1
    speaker['use_clone_settings'] = False
    custom = svc.plan(client.pid, body)['speakers'][0]
    assert custom['similarity'] == .2 and custom['stability'] == .1 and custom['speed'] == 1.1


def test_both_agents_can_clone_and_check_with_default_provider(client):
    saved = voice(client)
    svc = client.app_instance.state.voice_studio
    studio = Studio(client.pid, svc.store, svc.service, svc.legacy_jobs, lambda _: None, voice_studio=svc)
    tool = next(t for t in studio.tools() if t.name == 'voiceover_clone_and_check')
    started = client.portal.call(tool.handler, {'channel_voice_id': saved['id']})
    done = wait(client, json.loads(started)['id'])
    assert done['status'] == 'done' and done['result']['provider'] == 'chatterbox'


def test_agents_can_import_a_permitted_project_upload_but_not_reference_roles(client):
    upload = client.post(f'/api/projects/{client.pid}/files', files={'files': ('peter-upload.wav', wav_bytes(12))}).json()[0]
    svc = client.app_instance.state.voice_studio
    studio = Studio(client.pid, svc.store, svc.service, svc.legacy_jobs, lambda _: None, voice_studio=svc)
    tool = next(t for t in studio.tools() if t.name == 'voiceover_save_voice')
    with pytest.raises(ValueError, match='permission'):
        client.portal.call(tool.handler, {'name': 'Peter', 'file': upload['name'], 'consent': False})
    saved = json.loads(client.portal.call(tool.handler, {'name': 'Peter', 'file': upload['name'], 'consent': True}))
    assert saved['name'] == 'Peter' and not saved['providers']
    competitor = client.post(client.base + '/references', data={'kind': 'competitor'},
                             files={'file': ('competitor.wav', wav_bytes(12))}).json()
    with pytest.raises(ValueError, match='Competitor'):
        client.portal.call(tool.handler, {'name': 'Wrong source', 'file': './' + competitor['file'], 'consent': True})
    processed = client.post(client.base + f"/voices/{saved['id']}/processed",
                            files={'file': ('peter-radio.wav', wav_bytes(4, 1000))}).json()
    with pytest.raises(ValueError, match='processed radio'):
        client.portal.call(tool.handler, {'name': 'Wrong radio source', 'file': './' + processed['processed_file'], 'consent': True})
    assert not client.cloud and not client.local_requests


def test_worker_comparison_route_uses_uploaded_audio_not_client_paths(tmp_path, monkeypatch):
    import sys
    from voice import similarity
    fake_config = types.SimpleNamespace(JOBS=tmp_path)
    encoder = object()
    fake_engine = types.SimpleNamespace(_lock=threading.Lock(), _tts=types.SimpleNamespace(ve=encoder))
    observed = []
    def compare(reference, candidate, encoder=None):
        observed.append((reference.read_bytes(), candidate.read_bytes(), encoder))
        return score_embeddings([[1, 0]], [[1, 0]])
    monkeypatch.setattr(similarity, 'compare_files', compare)
    monkeypatch.setitem(sys.modules, 'config', fake_config)
    monkeypatch.setitem(sys.modules, 'engine', fake_engine)
    monkeypatch.setitem(sys.modules, 'similarity', similarity)
    spec = importlib.util.spec_from_file_location('isolated_voice_worker_server', Path(__file__).parents[2] / 'voice/server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app) as worker:
        result = worker.post('/api/compare-voice', files={'reference': ('../../secret.wav', b'original'),
                                                        'candidate': ('other-computer.wav', b'generated')})
    assert result.status_code == 200 and result.json()['speaker_cosine'] == 1
    assert observed == [(b'original', b'generated', encoder)]
    assert list(tmp_path.iterdir()) == []
