"""Shared Voiceovers backend for the dedicated page, Claude and Codex.

Provider credentials stay in keyring. Only explicit, one-request approval starts
billable analysis/generation. Reference analysis learns delivery, not model weights.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import re
import secrets
import shutil
import time
from pathlib import Path

import httpx
import keyring
import numpy as np

from . import media
from . import voice_studio_audio as audio
from .security.redact import redact, register_secret
from .storage import safe_filename
from .voice_studio_library import VoiceLibrary
from .voice_clone_check import CloneChecks

PROVIDERS = {'chatterbox': 'Chatterbox', 'elevenlabs': 'ElevenLabs', 'minimax': 'MiniMax'}
DEFAULT_MODELS = {'elevenlabs': 'eleven_multilingual_v2', 'minimax': 'speech-2.8-hd'}
AUDIO_TYPES = {'.wav', '.mp3', '.flac', '.m4a', '.ogg', '.aac', '.webm'}
MAX_REFERENCE = 100 * 1024 * 1024


class StudioError(ValueError):
    pass


def write_json(path: Path, value: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


class VoiceStudio(CloneChecks, VoiceLibrary):
    def __init__(self, store, settings, service, legacy_jobs, transport=None):
        self.store, self.settings, self.service, self.legacy_jobs = store, settings, service, legacy_jobs
        self.transport = transport
        self.tasks: dict[str, asyncio.Task] = {}
        self.live: dict[str, dict] = {}
        self.started = time.time()
        self.config_file = settings.data_dir / 'voice-studio-providers.json'

    def root(self, pid: str) -> Path:
        path = self.store.project_dir(pid) / 'voice-studio'
        path.mkdir(exist_ok=True)
        return path

    def key(self, provider: str) -> str:
        value = keyring.get_password('OneProduction', 'voice-studio:' + provider) or ''
        if value:
            register_secret(value)
        return value

    def config(self) -> dict:
        try:
            return json.loads(self.config_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def provider_status(self) -> list[dict]:
        config = self.config()
        return [{'id': name, 'label': label, 'key_set': bool(self.key(name)) if name != 'chatterbox' else False,
                 'model': (config.get(name) or {}).get('model') or DEFAULT_MODELS.get(name, ''),
                 'local': name == 'chatterbox',
                 'configured': not self.service.missing() if name == 'chatterbox' else bool(self.key(name)),
                 'detail': '; '.join(self.service.missing()) if name == 'chatterbox' else ''}
                for name, label in PROVIDERS.items()]

    def configure(self, provider: str, api_key: str | None, model: str | None, remove: bool = False) -> dict:
        if provider not in DEFAULT_MODELS:
            raise StudioError('Choose ElevenLabs or MiniMax for API settings.')
        if api_key is not None and api_key.strip():
            api_key = api_key.strip()
            if len(api_key) > 4096 or any(c.isspace() for c in api_key):
                raise StudioError('The API key must be one line without spaces.')
            register_secret(api_key)
            keyring.set_password('OneProduction', 'voice-studio:' + provider, api_key)
        if remove and self.key(provider):
            keyring.delete_password('OneProduction', 'voice-studio:' + provider)
        cfg = self.config()
        if model is not None:
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,90}', model):
                raise StudioError('Enter a valid provider model ID.')
            cfg[provider] = {'model': model}
            write_json(self.config_file, cfg)
        return next(p for p in self.provider_status() if p['id'] == provider)

    def client(self, timeout=180):
        return httpx.AsyncClient(transport=self.transport, timeout=timeout, follow_redirects=False)

    def ffmpeg(self) -> str:
        ff = media.find_ffmpeg(self.settings.data_dir)
        if not ff:
            raise StudioError('Install ffmpeg in Set up / modules before analyzing or generating audio.')
        return ff

    async def voices(self, provider: str) -> dict:
        if provider not in PROVIDERS:
            raise StudioError('Unknown voice provider.')
        if provider == 'chatterbox':
            if not await self.service.running():
                return {'voices': [], 'note': 'Start Chatterbox on the workstation that will generate narration.'}
            meta = await self.service.meta()
            return {'voices': [{'id': v, 'name': v} for v in meta.get('voices', []) if not v.startswith('_upload_')]}
        key = self.key(provider)
        if not key:
            return {'voices': [], 'note': 'Add this provider’s API key under Connections.'}
        if provider == 'minimax':
            async with self.client(30) as client:
                response = await client.post('https://api.minimax.io/v1/get_voice',
                                             headers={'Authorization': f'Bearer {key}'}, json={'voice_type': 'all'})
                self.check_response(response, 'MiniMax voice list')
                data = response.json()
            if (data.get('base_resp') or {}).get('status_code', 0):
                raise StudioError('MiniMax could not list these voices. Check the key and account permissions.')
            voices = [{'id': v['voice_id'], 'name': v.get('voice_name') or v['voice_id'], 'category': category}
                      for category in ('voice_cloning', 'voice_generation', 'system_voice')
                      for v in data.get(category, []) if v.get('voice_id')]
            return {'voices': voices, 'note': 'Your cloned/designed voices and system voices. Newly prepared MiniMax clones may only appear after their first use; saved channel voices retain their IDs.'}
        async with self.client(30) as client:
            response = await client.get('https://api.elevenlabs.io/v2/voices', params={'page_size': 100},
                                        headers={'xi-api-key': key})
            self.check_response(response, 'ElevenLabs voice list')
            data = response.json()
        return {'voices': [{'id': v['voice_id'], 'name': v.get('name') or v['voice_id']}
                           for v in data.get('voices', [])], 'note': 'First 100 voices; custom voice IDs are also accepted.'}

    @staticmethod
    def check_response(response, operation):
        if response.status_code >= 400:
            # Do not forward provider payloads or headers: they can include credentials or submitted text.
            hints = {401: 'Check the saved API key.', 403: 'Check the key permissions and provider plan.',
                     429: 'Provider rate or credit limit reached. No automatic retry was made.'}
            raise StudioError(f'{operation} returned HTTP {response.status_code}. ' + hints.get(response.status_code, 'Try again after checking the provider.'))

    def references(self, pid: str) -> list[dict]:
        return sorted([json.loads(p.read_text(encoding='utf-8')) for p in (self.root(pid) / 'references').glob('*.json')],
                      key=lambda r: r['created'], reverse=True)

    def reference(self, pid: str, rid: str) -> dict:
        if not re.fullmatch(r'[a-f0-9]{12}', rid):
            raise StudioError('No such reference.')
        path = self.root(pid) / 'references' / f'{rid}.json'
        if not path.is_file():
            raise StudioError('No such reference in this project.')
        return json.loads(path.read_text(encoding='utf-8'))

    async def add_reference(self, pid: str, name: str, data: bytes, kind: str, consent: bool) -> dict:
        ext = Path(name).suffix.lower()
        if ext not in AUDIO_TYPES or not data:
            raise StudioError('Upload WAV, MP3, FLAC, M4A, OGG, AAC or WebM audio.')
        if len(data) > MAX_REFERENCE:
            raise StudioError('Reference files must be under 100 MB.')
        if kind not in ('channel', 'competitor'):
            raise StudioError('Choose a channel sample or competitor delivery reference.')
        ff = self.ffmpeg()
        self.root(pid)
        rid = secrets.token_hex(6)
        filename = safe_filename(f'ref-{rid}-{name}')
        path = self.store.unique_path(pid, filename)
        path.write_bytes(data)
        try:
            seconds = await asyncio.to_thread(media.duration, ff, path)
            if not .5 <= seconds <= 3600:
                raise StudioError('Use a reference between half a second and 60 minutes.')
            peaks = await asyncio.to_thread(audio.waveform, ff, path)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        ref = {'id': rid, 'name': name[:180], 'file': path.name, 'kind': kind,
               'consent': bool(consent and kind == 'channel'), 'seconds': round(seconds, 3),
               'waveform': peaks, 'waveform_seconds': min(120, seconds), 'created': time.time(), 'analysis': None}
        self.save_reference(pid, ref)
        return ref

    def save_reference(self, pid, ref):
        write_json(self.root(pid) / 'references' / f"{ref['id']}.json", ref)

    def job(self, pid: str, jid: str) -> dict:
        if not re.fullmatch(r'[a-f0-9]{12}', jid):
            raise StudioError('No such voice studio job.')
        if jid in self.live and self.live[jid]['project'] == pid:
            return copy.deepcopy(self.live[jid])
        path = self.root(pid) / 'jobs' / f'{jid}.json'
        if not path.is_file():
            raise StudioError('No such job in this project.')
        job = json.loads(path.read_text(encoding='utf-8'))
        if job['status'] in ('queued', 'running') and job.get('created', 0) < self.started:
            job.update(status='stopped', step='Stopped by app restart. Review before starting another paid request.')
            self.save_job(job)
        return job

    def jobs(self, pid: str) -> list[dict]:
        result = [self.job(pid, p.stem) for p in (self.root(pid) / 'jobs').glob('*.json')]
        return sorted(result, key=lambda j: j['created'], reverse=True)[:40]

    def save_job(self, job):
        write_json(self.root(job['project']) / 'jobs' / f"{job['id']}.json", job)

    def launch(self, pid: str, kind: str, worker, **extra) -> dict:
        if any(j['project'] == pid and j['status'] in ('queued', 'running') for j in self.live.values()):
            raise StudioError('A voice studio job is already running in this project. Wait or cancel it first.')
        job = {'id': secrets.token_hex(6), 'project': pid, 'kind': kind, 'status': 'queued',
               'step': 'Queued', 'created': time.time(), 'files': [], 'error': None, **extra}
        self.live[job['id']] = job
        self.save_job(job)

        async def run():
            try:
                job.update(status='running', step='Starting')
                self.save_job(job)
                await worker(job)
                job.update(status='done', step='Ready for review')
            except asyncio.CancelledError:
                job.update(status='stopped', step='Stopped. Any completed provider request may still be billed.')
            except Exception as exc:
                job.update(status='failed', step='Failed', error=redact(str(exc))[:500])
            finally:
                self.save_job(job)
                self.tasks.pop(job['id'], None)
                self.live.pop(job['id'], None)
        self.tasks[job['id']] = asyncio.create_task(run())
        return copy.deepcopy(job)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def cancel(self, pid, jid):
        job = self.job(pid, jid)
        task = self.tasks.get(jid)
        if task:
            task.cancel()
        return {'requested': bool(task), 'job': job}

    def start_analysis(self, pid, rid, expected_speakers=2, language='', approved=False):
        if not approved:
            raise StudioError('Approve this analysis first: the reference is sent to ElevenLabs Scribe and uses API credits.')
        ref = self.reference(pid, rid)
        if expected_speakers not in (1, 2):
            raise StudioError('Choose one or two speakers.')
        key = self.key('elevenlabs')
        if not key:
            raise StudioError('Add an ElevenLabs API key under Connections for speaker analysis.')
        ff = self.ffmpeg()
        path = self.store.file_path(pid, ref['file'])

        async def analyze(job):
            job['step'] = 'Transcribing and separating speakers with ElevenLabs Scribe'
            data = {'model_id': 'scribe_v2', 'diarize': 'true', 'num_speakers': str(expected_speakers),
                    'timestamps_granularity': 'word', 'tag_audio_events': 'false'}
            if language:
                data['language_code'] = language[:12]
            async with self.client(600) as client:
                with path.open('rb') as stream:
                    response = await client.post('https://api.elevenlabs.io/v1/speech-to-text',
                                                 headers={'xi-api-key': key}, data=data,
                                                 files={'file': (path.name, stream, 'application/octet-stream')})
                self.check_response(response, 'Reference analysis')
                result = response.json()
            profile = audio.analyze_words(result.get('words') or [], ref['seconds'])
            profile['language'] = result.get('language_code', '')
            job['step'] = 'Measuring each speaker’s delivery and possible radio processing'
            for speaker in profile['speakers']:
                pieces = []
                remaining = 15.0
                for turn in profile['turns']:
                    if turn['speaker'] != speaker['id'] or remaining <= 0:
                        continue
                    length = min(remaining, turn['end'] - turn['start'])
                    pieces.append(await asyncio.to_thread(media.pcm, ff, path, 16000, turn['start'], length))
                    remaining -= length
                guess = audio.effect_guess(np.concatenate(pieces) if pieces else np.array([]))
                speaker['effect_analysis'] = guess
                speaker['effect'] = guess['suggested']
            if len(profile['speakers']) != expected_speakers:
                profile['notes'].append(f"Expected {expected_speakers} speakers; detected {len(profile['speakers'])}. Review this reference before using it.")
            ref['analysis'] = profile
            self.save_reference(pid, ref)
            job['reference_id'] = rid
            job['result'] = profile
        return self.launch(pid, 'analysis', analyze, reference_id=rid, name=ref['name'])

    def update_profile(self, pid, rid, changes):
        ref = self.reference(pid, rid)
        if not ref.get('analysis'):
            raise StudioError('Analyze this reference first.')
        by_id = {s['id']: s for s in ref['analysis']['speakers']}
        for change in changes:
            if change['id'] not in by_id:
                raise StudioError('Unknown speaker in this reference.')
            speaker = by_id[change['id']]
            label = change.get('label', speaker['label']).strip()[:40]
            effect = change.get('effect', speaker['effect'])
            wpm = float(change.get('wpm') or speaker['wpm'])
            if not label or effect not in ('clean', 'radio') or not 60 <= wpm <= 350:
                raise StudioError('Use a speaker name, clean/radio effect and WPM between 60 and 350.')
            speaker.update(label=label, effect=effect, target_wpm=wpm)
        labels = [s['label'].casefold() for s in by_id.values()]
        if len(set(labels)) != len(labels):
            raise StudioError('Give the speakers different names.')
        self.save_reference(pid, ref)
        return ref

    async def clone_reference(self, pid, rid, sid, consent):
        ref = self.reference(pid, rid)
        if ref['kind'] != 'channel' or not ref['consent'] or not consent:
            raise StudioError('Only your own channel samples with voice-cloning permission can be used here. Competitor clips are delivery references.')
        profile = ref.get('analysis') or {}
        speaker = next((s for s in profile.get('speakers', []) if s['id'] == sid), None)
        if not speaker:
            raise StudioError('Analyze the reference and choose a detected speaker first.')
        if profile.get('overlapping_words'):
            raise StudioError('This sample contains overlapping speech. Upload a clean, isolated speaker sample for cloning.')
        ff = self.ffmpeg()
        work = self.root(pid) / 'clone' / secrets.token_hex(6)
        work.mkdir(parents=True)
        clips, remaining = [], 30.0
        source = self.store.file_path(pid, ref['file'])
        for i, turn in enumerate(profile['turns']):
            if turn['speaker'] != sid or remaining <= 0:
                continue
            length = min(remaining, turn['end'] - turn['start'])
            target = work / f'{i}.wav'
            result = await asyncio.to_thread(media.run, [ff, '-y', '-v', 'error', '-ss', str(turn['start']),
                                                       '-i', str(source), '-t', str(length), '-ac', '1', '-ar', '48000',
                                                       '-c:a', 'pcm_s16le', str(target)], 120)
            if result.returncode:
                raise StudioError('Could not extract this speaker’s sample.')
            clips.append({'path': target, 'speaker': sid})
            remaining -= length
        if 30 - remaining < 8:
            raise StudioError('This speaker has less than 8 seconds of usable audio. Upload a longer clean reference.')
        sample = work / 'reference.wav'
        await asyncio.to_thread(audio.join_turns, clips, sample)
        await self.service.ensure_running()
        result = await self.service.add_voice(f'channel-{rid}-{sid}.wav', sample.read_bytes())
        return {**result, 'note': 'Processed/radio references can color the cloned voice. A clean isolated sample gives better control.'}

    def plan(self, pid, spec: dict) -> dict:
        using_reviewed_plan = bool(spec.get('use_reviewed_plan'))
        if using_reviewed_plan:
            if set(spec) - {'use_reviewed_plan', 'approve'}:
                raise StudioError('Use the reviewed plan on its own, or provide a new script and settings to review.')
            saved = self.last_reviewed_plan(pid)
            if not saved:
                raise StudioError('Open Voiceovers and click Review for this project first.')
            # Use the reviewed text snapshot even if its source file has since changed.
            spec = {**saved, 'text': saved['script_text'], 'file': ''}
        text = spec.get('text') or ''
        source = spec.get('file') or ''
        if source:
            from .scripts import read_script
            text = read_script(self.store.file_path(pid, source))
        if not text.strip() or len(text) > 100000:
            raise StudioError('Provide a script between 1 and 100,000 characters.')
        if spec.get('format', 'shorts') not in ('shorts', 'longform'):
            raise StudioError('Choose Shorts or long-form.')
        speakers = copy.deepcopy(spec.get('speakers') or [])
        if not 1 <= len(speakers) <= 2:
            raise StudioError('Configure one or two speakers.')
        ids = set()
        profile = None
        if spec.get('reference_id'):
            profile = self.reference(pid, spec['reference_id']).get('analysis')
            if not profile:
                raise StudioError('Analyze the selected delivery reference first.')
        learned = {s['id']: s for s in (profile or {}).get('speakers', [])}
        for i, speaker in enumerate(speakers):
            speaker['id'] = str(speaker.get('id') or chr(65 + i))
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', speaker['id']) or speaker['id'] in ids:
                raise StudioError('Use different speaker IDs containing letters, numbers, underscores or dashes.')
            ids.add(speaker['id'])
            speaker['label'] = str(speaker.get('label') or speaker['id']).strip()[:40]
            if not speaker['label']:
                raise StudioError('Give each speaker a name.')
            if speaker.get('provider') not in PROVIDERS:
                raise StudioError('Choose Chatterbox, ElevenLabs or MiniMax for each speaker.')
            if speaker.get('channel_voice_id'):
                identity = self.channel_voice(pid, speaker['channel_voice_id'])
                binding = identity['providers'].get(speaker['provider'])
                if not binding:
                    raise StudioError(f"Prepare {identity['name']} for {PROVIDERS[speaker['provider']]} in the voice library first.")
                if binding.get('requires_verification'):
                    raise StudioError(f"{identity['name']} requires provider verification before use. Complete it with the provider and enter its verified voice ID manually.")
                if using_reviewed_plan and speaker.get('voice_id') != binding['id']:
                    raise StudioError('A reviewed voice binding changed. Review the script and settings again before generation.')
                speaker['voice_id'] = binding['id']
                tuning = binding.get('generation_settings') or {}
                if not using_reviewed_plan and speaker.get('use_clone_settings', True):
                    for key in ('model', 'speed', 'stability', 'similarity', 'style', 'pitch', 'volume'):
                        if key in tuning:
                            speaker[key] = tuning[key]
                if not using_reviewed_plan and speaker['provider'] == 'chatterbox' and tuning:
                    speaker['chatterbox_settings'] = {k: tuning[k] for k in ('exaggeration', 'cfg_weight', 'temperature') if k in tuning}
                    speaker['seed'] = tuning.get('seed', 4242)
                if not using_reviewed_plan and speaker.get('effect') == 'radio' and identity.get('effect_profile'):
                    speaker['effect_settings'] = {k: identity['effect_profile'][k] for k in
                                                  ('highpass_hz', 'lowpass_hz', 'compression_ratio')}
            voice_id = str(speaker.get('voice_id') or '').strip()
            if not voice_id or len(voice_id) > 180 or not re.fullmatch(r'[\w .(),-]+', voice_id):
                raise StudioError('Choose or enter a valid voice ID for each speaker.')
            speaker['voice_id'] = voice_id
            delivery = learned.get(speaker.get('reference_speaker')) or {}
            speaker['wpm'] = float(speaker.get('wpm') or delivery.get('target_wpm') or delivery.get('wpm') or 160)
            speaker['effect'] = speaker.get('effect') or delivery.get('effect') or 'clean'
            speaker['gap_ms'] = int(speaker.get('gap_ms') if speaker.get('gap_ms') is not None else
                                    (profile or {}).get('pauses', {}).get('turn_gap_ms', 250))
            learned_pause = (profile or {}).get('pauses', {}).get('within_speaker_median_seconds')
            speaker['pause_ms'] = speaker.get('pause_ms')
            if speaker['pause_ms'] is None and learned_pause is not None:
                speaker['pause_ms'] = min(2000, round(learned_pause * 1000))
            if speaker['pause_ms'] is not None and not 0 <= float(speaker['pause_ms']) <= 2000:
                raise StudioError('Internal speech pauses must be between 0 and 2000 ms, or left natural.')
            if not 60 <= speaker['wpm'] <= 350 or speaker['effect'] not in ('clean', 'radio', 'miles_radio') or not 0 <= speaker['gap_ms'] <= 5000:
                raise StudioError('Use WPM 60–350, clean/radio effect and a turn gap of 0–5000 ms.')
            speaker['use_target_wpm'] = bool(speaker.get('use_target_wpm', True))
            speaker['speed'] = float(speaker.get('speed', 1))
            speaker['volume'] = float(speaker.get('volume', 1))
            speaker['pitch'] = int(speaker.get('pitch', 0))
            for field, default in (('stability', .5), ('similarity', .75), ('style', 0)):
                speaker[field] = float(speaker.get(field, default))
                if not 0 <= speaker[field] <= 1:
                    raise StudioError('Stability, similarity and style must be between 0 and 1.')
            if not .5 <= speaker['speed'] <= 2 or not .1 <= speaker['volume'] <= 3 or not -12 <= speaker['pitch'] <= 12:
                raise StudioError('Check speed, pitch and volume ranges.')
            speaker['model'] = str(speaker.get('model') or (self.config().get(speaker['provider']) or {}).get('model') or DEFAULT_MODELS.get(speaker['provider'], 'chatterbox'))
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,90}', speaker['model']):
                raise StudioError('Enter a valid model ID.')
        episode = str(spec.get('episode_number') or '').strip().lstrip('#')
        if episode and not re.fullmatch(r'\d{1,7}', episode):
            raise StudioError('Use digits for the episode number, for example 19.')
        if not 0 <= int(spec.get('pause_ms', 180)) <= 2000 or not 0 <= int(spec.get('turn_gap_ms', 250)) <= 5000:
            raise StudioError('Check the production pause and speaker gap settings.')
        if not -65 <= float(spec.get('silence_threshold_db', -42)) <= -20:
            raise StudioError('Silence threshold must be between -65 and -20 dBFS.')
        turns = audio.script_turns(text, speakers)
        if spec.get('preview'):
            turns = turns[:2]
            for turn in turns:
                turn['text'] = ' '.join(turn['text'].split()[:40])
        lookup = {s['id']: s for s in speakers}
        estimated = sum(audio.word_count(t['text']) * 60 / (lookup[t['speaker']]['wpm'] if lookup[t['speaker']]['use_target_wpm'] else 160 * lookup[t['speaker']]['speed']) +
                        (lookup[t['speaker']]['gap_ms'] / 1000 if t['turn_end'] else .12) for t in turns)
        plan = {'name': str(spec.get('name') or 'Voiceover')[:80], 'format': spec.get('format', 'shorts'),
                'preview': bool(spec.get('preview')), 'speakers': speakers, 'turns': turns,
                'source': source, 'script_text': text, 'reference_id': spec.get('reference_id') or '',
                'episode_number': episode, 'consistent_pauses': bool(spec.get('consistent_pauses', True)),
                'pause_ms': int(spec.get('pause_ms', 180)), 'turn_gap_ms': int(spec.get('turn_gap_ms', 250)),
                'silence_threshold_db': float(spec.get('silence_threshold_db', -42)),
                'export_stems': bool(spec.get('export_stems', True)),
                'words': sum(audio.word_count(t['text']) for t in turns), 'characters': sum(len(t['text']) for t in turns),
                'estimated_seconds': round(estimated, 1), 'requests': len(turns),
                'paid_providers': sorted({s['provider'] for s in speakers if s['provider'] != 'chatterbox'}),
                'note': 'Generation follows the reviewed speaker mapping. WPM is measured across each rendered turn; extreme changes are limited to preserve intelligibility.'}
        write_json(self.root(pid) / 'last-reviewed-plan.json', plan)
        return plan

    def last_reviewed_plan(self, pid):
        try:
            return json.loads((self.root(pid) / 'last-reviewed-plan.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def start_generation(self, pid, spec, approved=False):
        if not approved:
            raise StudioError('Approve this generation first. Cloud voices consume credits; Chatterbox uses the voice workstation.')
        plan = self.plan(pid, spec)
        self.ffmpeg()
        for provider in plan['paid_providers']:
            if not self.key(provider):
                raise StudioError(f'Add the {PROVIDERS[provider]} API key under Connections.')
        return self.launch(pid, 'generation', lambda job: self.generate(pid, job, plan), name=plan['name'], plan=plan)

    def start_repair(self, pid, source_job_id, sections, reason='', approved=False):
        if not approved:
            raise StudioError('Approve this passage repair first. It generates only the selected sections and can use voice credits.')
        source = self.job(pid, source_job_id)
        if source['status'] != 'done' or source['kind'] != 'generation':
            raise StudioError('Choose a completed voiceover to repair.')
        plan = copy.deepcopy(source['plan'])
        selected = sorted(set(sections))
        if not selected or len(selected) > 20 or any(type(i) is not int or not 1 <= i <= len(plan['turns']) for i in selected):
            raise StudioError('Choose 1–20 valid section numbers from this take.')
        old_work = self.root(pid) / 'work' / source_job_id
        for i in range(len(plan['turns'])):
            if i + 1 not in selected and not all((old_work / f'{part}-{i}.{ext}').is_file()
                                                for part, ext in [('raw', 'audio'), ('alignment', 'json')]):
                raise StudioError('This take has no reusable source audio for passage repair. Make a new take first.')
        self.ffmpeg()
        affected = {plan['turns'][i - 1]['speaker'] for i in selected}
        for speaker in plan['speakers']:
            if speaker['id'] in affected and speaker['provider'] != 'chatterbox' and not self.key(speaker['provider']):
                raise StudioError(f"Add the {PROVIDERS[speaker['provider']]} API key under Connections.")
        return self.launch(pid, 'generation', lambda job: self.generate(pid, job, plan, old_work, selected),
                           name=plan['name'], plan=plan, source_job_id=source_job_id,
                           repaired_sections=selected, repair_reason=str(reason).strip()[:1000])

    async def synthesize(self, speaker, text, destination):
        provider = speaker['provider']
        if provider == 'chatterbox':
            await self.service.ensure_running()
            meta = await self.service.meta()
            if speaker['voice_id'] not in meta.get('voices', []):
                raise StudioError(f"Chatterbox reference {speaker['voice_id']} was not found on this workstation.")
            jid = await self.service.submit({'script': text, 'voice': speaker['voice_id'], 'name': destination.stem,
                                             'preset': 'clone_exact', 'pace_mode': 'natural', 'auto_fix': True,
                                             'seed': speaker.get('seed', 4242),
                                             **(speaker.get('chatterbox_settings') or {})})
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                result = await self.service.job(jid)
                if result.get('status') == 'failed':
                    raise StudioError('Chatterbox failed: ' + str(result.get('error') or 'unknown error'))
                if result.get('status') == 'done':
                    # HTTP output works without assuming a worker's filesystem is mounted locally.
                    async with self.service._client(180) as client:
                        response = await client.get(f'{self.service.url}/api/preview/{jid}/wav')
                        self.check_response(response, 'Chatterbox output')
                        destination.write_bytes(response.content)
                    write_json(destination.with_suffix('.qc.json'), {'kind': 'chatterbox_transcript',
                        'sections': [{k: s.get(k) for k in ('status', 'wer', 'diffs', 'seed')}
                                     for s in result.get('sections', [])],
                        'note': 'Transcript consistency only; not an auditory judgment of pronunciation or performance.'})
                    return []
                await asyncio.sleep(1)
            raise StudioError('Chatterbox exceeded 30 minutes for one section. Check its workstation before retrying.')
        key = self.key(provider)
        model = speaker.get('model') or (self.config().get(provider) or {}).get('model') or DEFAULT_MODELS[provider]
        async with self.client(240) as client:
            if provider == 'elevenlabs':
                response = await client.post(f"https://api.elevenlabs.io/v1/text-to-speech/{speaker['voice_id']}/with-timestamps",
                                             headers={'xi-api-key': key}, params={'output_format': 'mp3_44100_128'},
                                             json={'text': text, 'model_id': model,
                                                   'voice_settings': {'stability': speaker.get('stability', .5),
                                                       'similarity_boost': speaker.get('similarity', .75),
                                                       'style': speaker.get('style', 0), 'speed': speaker.get('speed', 1)}})
                self.check_response(response, 'ElevenLabs generation')
                result = response.json()
                try:
                    destination.write_bytes(base64.b64decode(result['audio_base64'], validate=True))
                except (KeyError, ValueError):
                    raise StudioError('ElevenLabs returned an invalid audio payload.') from None
                return audio.alignment_words(result.get('normalized_alignment') or result.get('alignment'))
            response = await client.post('https://api.minimax.io/v1/t2a_v2', headers={'Authorization': f'Bearer {key}'},
                                         json={'model': model, 'text': text, 'stream': False, 'output_format': 'hex',
                                               'voice_setting': {'voice_id': speaker['voice_id'], 'speed': speaker.get('speed', 1),
                                                                 'vol': speaker.get('volume', 1), 'pitch': speaker.get('pitch', 0)},
                                               'audio_setting': {'sample_rate': 32000, 'format': 'mp3', 'channel': 1}})
            self.check_response(response, 'MiniMax generation')
            result = response.json()
            if (result.get('base_resp') or {}).get('status_code', 0) != 0:
                raise StudioError('MiniMax refused the request. Check the model, voice ID and available credits.')
            try:
                destination.write_bytes(bytes.fromhex(result['data']['audio']))
            except (KeyError, ValueError, TypeError):
                raise StudioError('MiniMax returned an invalid audio payload.') from None
            return []

    async def generate(self, pid, job, plan, reuse=None, repair_sections=None):
        ff = self.ffmpeg()
        work = self.root(pid) / 'work' / job['id']
        work.mkdir(parents=True)
        speakers = {s['id']: s for s in plan['speakers']}
        timeline, preproduction, cursor, flags = [], [], 0.0, []
        for i, turn in enumerate(plan['turns']):
            speaker = speakers[turn['speaker']]
            reused = reuse is not None and i + 1 not in repair_sections
            job.update(step=f"{'Reusing' if reused else 'Generating'} section {i + 1}/{len(plan['turns'])} · {speaker['label']}",
                       completed=i, total=len(plan['turns']))
            self.save_job(job)
            raw, processed = work / f'raw-{i}.audio', work / f'turn-{i}.wav'
            if reused:
                shutil.copyfile(reuse / raw.name, raw)
                alignment = json.loads((reuse / f'alignment-{i}.json').read_text(encoding='utf-8'))
                if (reuse / raw.with_suffix('.qc.json').name).is_file():
                    shutil.copyfile(reuse / raw.with_suffix('.qc.json').name, raw.with_suffix('.qc.json'))
            else:
                request_speaker = {**speaker, 'seed': 4243 + secrets.randbelow(2**31 - 4243)} if reuse is not None else speaker
                alignment = await self.synthesize(request_speaker, turn['text'], raw)
            write_json(work / f'alignment-{i}.json', alignment)
            native_check = json.loads(raw.with_suffix('.qc.json').read_text(encoding='utf-8')) if raw.with_suffix('.qc.json').is_file() else None
            original, seconds = await asyncio.to_thread(audio.render_turn, ff, raw, processed,
                                                        audio.word_count(turn['text']), speaker['wpm'] if speaker['use_target_wpm'] else None, speaker['effect'],
                                                        speaker.get('effect_settings'))
            if speaker['provider'] == 'chatterbox' and not speaker['use_target_wpm'] and abs(speaker['speed'] - 1) > .001:
                sped = work / f'sped-{i}.wav'
                result = await asyncio.to_thread(media.run, [ff, '-y', '-v', 'error', '-i', str(processed), '-af',
                                                             f"atempo={speaker['speed']}", '-c:a', 'pcm_s16le', str(sped)], 180)
                if result.returncode:
                    raise StudioError('Could not apply Chatterbox speed.')
                sped.replace(processed)
                seconds = await asyncio.to_thread(media.duration, ff, processed)
            scale = seconds / original
            for word in alignment:
                word['start'] *= scale
                word['end'] *= scale
            before_ripple = work / f'preprod-{i}.wav'
            shutil.copyfile(processed, before_ripple)
            gap = (turn.get('explicit_gap_ms', plan['turn_gap_ms'] if plan['consistent_pauses'] else speaker['gap_ms']) if turn['turn_end'] or 'explicit_gap_ms' in turn else 120) if i + 1 < len(plan['turns']) else 0
            preproduction.append({**turn, 'path': before_ripple, 'seconds': seconds, 'gap_ms': gap})
            seconds = await asyncio.to_thread(audio.shape_pauses, processed,
                speaker['pause_ms'] if speaker['pause_ms'] is not None else plan['pause_ms'] if plan['consistent_pauses'] else None,
                alignment, plan['consistent_pauses'], plan['silence_threshold_db'])
            timeline.append({**turn, 'section': i + 1, 'path': processed, 'seconds': seconds, 'start': cursor, 'gap_ms': gap,
                             'alignment': alignment, 'timing': 'provider' if alignment else 'estimated',
                             'transcript_check': native_check, 'reused_audio': reused})
            if native_check and any(s.get('status') == 'check' for s in native_check['sections']):
                flags.append(f'Section {i + 1}: Chatterbox still reports a transcript mismatch after its bounded retries. Review this passage.')
            measured_wpm = audio.word_count(turn['text']) * 60 / max(seconds, .01)
            if speaker['use_target_wpm'] and abs(measured_wpm - speaker['wpm']) / speaker['wpm'] > .15:
                flags.append(f"Section {i + 1}: measured {measured_wpm:.0f} WPM versus target {speaker['wpm']:.0f}; timing changes were limited to keep speech intelligible.")
            cursor += seconds + gap / 1000
        job['step'] = 'Assembling speakers, radio effects and subtitles'
        joined = work / 'joined.wav'
        await asyncio.to_thread(audio.join_turns, timeline, joined)
        prefix = safe_filename(f"#{plan['episode_number']} VO {'Preview' if plan['preview'] else 'Final'}" if plan['episode_number']
                               else f"{plan['name']}{' Preview' if plan['preview'] else ' VO Final'}")[:110]
        wav = self.store.unique_path(pid, prefix + '.wav')
        mp3 = self.store.unique_path(pid, prefix + '.mp3')
        srt = self.store.unique_path(pid, prefix + '.srt')
        manifest = self.store.unique_path(pid, prefix + '-delivery.json')
        preprod = self.store.unique_path(pid, (f"#{plan['episode_number']} VO Preprod" if plan['episode_number'] and not plan['preview'] else prefix + ' Preprod') + '.wav')
        await asyncio.to_thread(audio.join_turns, preproduction, preprod)
        job['step'] = 'Mastering to −14 LUFS and exporting audio'
        loud = await asyncio.to_thread(audio.master_delivery, ff, joined, wav)
        response = await asyncio.to_thread(media.run, [ff, '-y', '-v', 'error', '-i', str(wav), '-codec:a',
                                                      'libmp3lame', '-b:a', '192k', str(mp3)], 300)
        if response.returncode:
            raise StudioError('MP3 export failed. The provider audio remains in the project work folder.')
        srt.write_text(audio.srt_text(timeline), encoding='utf-8')
        subtitle_timing = 'estimated' if any(t['timing'] == 'estimated' for t in timeline) else 'provider'
        if subtitle_timing == 'estimated' and self.service.python and self.service.python.is_file() and self.service.app_dir:
            script_path = work / 'spoken-script.txt'
            script_path.write_text('\n\n'.join(t['text'] for t in timeline), encoding='utf-8')
            job['step'] = 'Aligning subtitles with the voice workstation’s local speech recognizer'
            try:
                await self.service.make_srt(wav, script_path, srt)
                subtitle_timing = 'local_asr'
            except Exception:
                # Preserve the usable estimated file and explain the failure without another billed request.
                srt.write_text(audio.srt_text(timeline), encoding='utf-8')
                flags.append('Local subtitle alignment failed; estimated timings were retained for review.')
        if subtitle_timing == 'estimated':
            flags.append('Some subtitles use estimated timing. Review them before publishing; no extra paid transcription was run.')
        flags.append('Generated speech has not passed an independent transcript/read-check. Listen and review names, numbers and pronunciation.')
        seconds = await asyncio.to_thread(media.duration, ff, wav)
        mp3_loud = await asyncio.to_thread(media.loudness, ff, mp3)
        if mp3_loud['true_peak'] > -1:
            # Local mastering retries do not regenerate or bill the TTS providers.
            job['step'] = 'Correcting encoded true peak and rechecking delivery levels'
            loud = await asyncio.to_thread(audio.master_delivery, ff, joined, wav, -2.0)
            response = await asyncio.to_thread(media.run, [ff, '-y', '-v', 'error', '-i', str(wav), '-codec:a',
                                                          'libmp3lame', '-b:a', '192k', str(mp3)], 300)
            if response.returncode:
                raise StudioError('Corrected MP3 export failed.')
            mp3_loud = await asyncio.to_thread(media.loudness, ff, mp3)
        if abs(loud['lufs'] + 14) > .5 or loud['true_peak'] > -1:
            flags.append('Final loudness or peak misses the delivery target. Review the measured levels.')
        if abs(mp3_loud['lufs'] + 14) > .5 or mp3_loud['true_peak'] > -1:
            flags.append('The MP3 encode misses a loudness/peak target. Use the WAV for editing and review the delivery report.')
        if any(s['effect'] == 'miles_radio' for s in speakers.values()):
            flags.append('Miles uses the saved Resolve values through a portable distortion approximation. Compare against your processed sample before first production use.')
        stems = []
        if plan['export_stems'] and len(speakers) > 1:
            for sid, speaker in speakers.items():
                stem = self.store.unique_path(pid, prefix + '-' + safe_filename(speaker['label']) + '-stem.wav')
                await asyncio.to_thread(audio.join_turns, timeline, stem, sid)
                stems.append(stem.name)
        delivery = {'plan': plan, 'seconds': seconds, 'loudness': loud, 'mp3_loudness': mp3_loud,
                    'source_job_id': job.get('source_job_id'), 'repaired_sections': job.get('repaired_sections', []),
                    'repair_reason': job.get('repair_reason', ''), 'review_status': 'needs_listening_review',
                    'targets': {'lufs': -14, 'true_peak_ceiling': -1}, 'flags': flags, 'subtitle_timing': subtitle_timing,
                    'stems_stage': 'Processed and silence-edited, before final mix mastering. Stems are optional editing aids.',
                    'miles_distortion_reference': audio.MILES_RESOLVE_PRESET if any(s['effect'] == 'miles_radio' for s in speakers.values()) else None,
                    'timeline': [{k: v for k, v in t.items() if k != 'path'} for t in timeline]}
        write_json(manifest, delivery)
        files = [wav.name, mp3.name, srt.name, manifest.name, preprod.name, *stems]
        result = {'seconds': round(seconds, 3), 'words': plan['words'],
                  'wpm': round(plan['words'] * 60 / max(seconds, .1), 1),
                  'clock': f'{int(seconds) // 60}:{int(seconds) % 60:02}', 'flagged': flags, 'loudness': loud,
                  'subtitle_timing': subtitle_timing, 'mp3_loudness': mp3_loud,
                  'review_status': 'needs_listening_review',
                  'sections': [{k: v for k, v in t.items() if k not in ('path', 'alignment')} for t in timeline],
                  'delivery_passed': abs(loud['lufs'] + 14) <= .5 and loud['true_peak'] <= -1}
        legacy = self.legacy_jobs.register_finished(pid, plan['name'], files, result, source=plan['source'],
                                                    job_id=job['id'])
        job.update(files=files, result=result, voice_job_id=legacy['id'], completed=len(timeline),
                   total=len(timeline), flags=flags)
