"""Named clean channel voices and separately paired processed references.

Mixed competitor recordings never enter this library. Clean voice identity is used
for cloning; processed samples only guide optional post-processing.
"""
import asyncio
import json
import re
import secrets
import time

import numpy as np

from . import media
from .storage import safe_filename
from .voice_studio_audio import effect_guess, fit_radio_bandwidth


class VoiceLibrary:
    def channel_voices(self, pid):
        return sorted([json.loads(p.read_text(encoding='utf-8')) for p in (self.root(pid) / 'voices').glob('*.json')],
                      key=lambda v: v['name'].casefold())

    def channel_voice(self, pid, vid):
        if not re.fullmatch(r'[a-f0-9]{12}', vid):
            raise ValueError('No such saved channel voice.')
        path = self.root(pid) / 'voices' / f'{vid}.json'
        if not path.is_file():
            raise ValueError('No such channel voice in this project.')
        return json.loads(path.read_text(encoding='utf-8'))

    def save_channel_voice(self, pid, value):
        from .voice_studio import write_json
        write_json(self.root(pid) / 'voices' / f"{value['id']}.json", value)

    async def import_channel_voice(self, pid, name, file, consent):
        """Allow agents to pick up an ordinary project upload, without leaving this project."""
        if not consent:
            raise ValueError('Confirm permission to clone this clean, single-speaker recording.')
        path = self.store.file_path(pid, file)
        folder = self.store.files_dir(pid).resolve()
        same_source = lambda name: bool(name) and (folder / name).resolve() == path
        if any(same_source(r['file']) and r['kind'] == 'competitor' for r in self.references(pid)):
            raise ValueError('Competitor files are delivery references, not voice-cloning sources.')
        if any(same_source(v.get('processed_file')) for v in self.channel_voices(pid)):
            raise ValueError('Use the clean identity recording, not a processed radio reference.')
        if path.stat().st_size > 100 * 1024 * 1024:
            raise ValueError('Clean voice uploads must be under 100 MB.')
        return await self.add_channel_voice(pid, name, path.name, await asyncio.to_thread(path.read_bytes), True)

    async def add_channel_voice(self, pid, name, filename, data, consent):
        if not consent:
            raise ValueError('Confirm that you have permission to clone this clean voice sample.')
        name = name.strip()[:40]
        if not name:
            raise ValueError('Name this voice, for example Peter or Miles.')
        if any(v['name'].casefold() == name.casefold() for v in self.channel_voices(pid)):
            raise ValueError('A voice with this name already exists in this project.')
        ref = await self.add_reference(pid, filename, data, 'channel', True)
        if not 8 <= ref['seconds'] <= 300:
            self.store.file_path(pid, ref['file']).unlink(missing_ok=True)
            (self.root(pid) / 'references' / f"{ref['id']}.json").unlink(missing_ok=True)
            raise ValueError('Use a clean single-speaker sample between 8 seconds and 5 minutes. About one minute is welcome.')
        value = {'id': secrets.token_hex(6), 'name': name, 'clean_file': ref['file'], 'seconds': ref['seconds'],
                 'clean_reference_id': ref['id'], 'consent': True, 'created': time.time(),
                 'providers': {}, 'processed_file': '', 'effect_profile': None}
        self.save_channel_voice(pid, value)
        return value

    async def add_processed_sample(self, pid, vid, filename, data):
        voice = self.channel_voice(pid, vid)
        ref = await self.add_reference(pid, filename, data, 'channel', False)
        ff = self.ffmpeg()
        clean = await asyncio.to_thread(media.pcm, ff, self.store.file_path(pid, voice['clean_file']), 16000, 0, 30)
        processed = await asyncio.to_thread(media.pcm, ff, self.store.file_path(pid, ref['file']), 16000, 0, 30)
        baseline, target = effect_guess(clean), effect_guess(processed)
        reductions = {}
        for band in ('low_band_ratio', 'high_band_ratio'):
            if band in baseline and band in target:
                reductions[band] = round(10 * float(np.log10((target[band] + .00001) / (baseline[band] + .00001))), 2)
        fitted = await asyncio.to_thread(fit_radio_bandwidth, clean, processed)
        profile = {'type': 'radio', **fitted,
                   'clean_analysis': baseline, 'processed_analysis': target, 'relative_band_db': reductions,
                   'confidence': 'approximation',
                   'note': 'Clean and processed voice are stored separately. Bandwidth comparison suggests a starting radio preset, not the original effect chain. Compare a preview by ear; different words/backgrounds affect these measurements.'}
        voice = self.channel_voice(pid, vid)
        voice.update(processed_file=ref['file'], processed_reference_id=ref['id'], effect_profile=profile)
        self.save_channel_voice(pid, voice)
        return voice

    def prepare_channel_voice(self, pid, vid, provider, approved=False):
        if not approved:
            raise ValueError('Approve preparing this voice. Cloud cloning uploads the clean sample and may use provider credits/voice slots.')
        if provider not in ('chatterbox', 'elevenlabs', 'minimax'):
            raise ValueError('Choose Chatterbox, ElevenLabs or MiniMax.')
        voice = self.channel_voice(pid, vid)
        if provider in voice['providers']:
            raise ValueError('This voice already has an ID for that provider. Use the saved clone rather than creating another.')
        if provider != 'chatterbox' and not self.key(provider):
            raise ValueError('Add this provider’s API key under Connections first.')
        ff = self.ffmpeg()

        return self.launch(pid, 'voice_clone', lambda job: self._prepare_channel_voice(pid, vid, provider, job),
                           name=f"{voice['name']} · {provider}")

    async def _prepare_channel_voice(self, pid, vid, provider, job):
        """Prepare once; shared by preparation-only and the bounded clone/check workflow."""
        voice = self.channel_voice(pid, vid)
        ff = self.ffmpeg()
        work = self.root(pid) / 'work' / job['id']
        work.mkdir(parents=True)
        sample = work / 'clean-voice.wav'
        job['step'] = 'Preparing the clean identity sample (the radio sample is not used for cloning)'
        result = await asyncio.to_thread(media.run, [ff, '-y', '-v', 'error', '-i',
            str(self.store.file_path(pid, voice['clean_file'])), '-t', '30' if provider == 'chatterbox' else '120',
            '-ac', '1', '-ar', '32000', '-c:a', 'pcm_s16le', str(sample)], 120)
        if result.returncode:
            raise ValueError('Could not prepare the clean voice recording.')
        verification, provider_note = False, ''
        if provider == 'chatterbox':
            await self.service.ensure_running()
            result = await self.service.add_voice(f"{safe_filename(voice['name'])}-{vid}.wav", sample.read_bytes())
            voice_id = result['name']
        else:
            async with self.client(300) as client:
                if provider == 'elevenlabs':
                    with sample.open('rb') as stream:
                        response = await client.post('https://api.elevenlabs.io/v1/voices/add',
                            headers={'xi-api-key': self.key(provider)}, data={'name': voice['name'],
                            'description': 'Permitted channel voice imported by One Production'},
                            files={'files': ('clean-voice.wav', stream, 'audio/wav')})
                    self.check_response(response, 'ElevenLabs voice cloning')
                    result = response.json()
                    voice_id, verification = result['voice_id'], bool(result.get('requires_verification'))
                    if verification:
                        provider_note = 'Complete the provider’s verification before synthesis; no verification is bypassed.'
                else:
                    headers = {'Authorization': 'Bearer ' + self.key(provider)}
                    with sample.open('rb') as stream:
                        response = await client.post('https://api.minimax.io/v1/files/upload', headers=headers,
                            data={'purpose': 'voice_clone'}, files={'file': ('clean-voice.wav', stream, 'audio/wav')})
                    self.check_response(response, 'MiniMax clean sample upload')
                    upload = response.json()
                    if (upload.get('base_resp') or {}).get('status_code', 0):
                        raise ValueError('MiniMax refused the clone sample upload.')
                    voice_id = 'op' + vid + secrets.token_hex(3)
                    response = await client.post('https://api.minimax.io/v1/voice_clone', headers=headers,
                        json={'file_id': int(upload['file']['file_id']), 'voice_id': voice_id,
                              'need_noise_reduction': False, 'need_volume_normalization': False})
                    self.check_response(response, 'MiniMax voice cloning')
                    result = response.json()
                    if (result.get('base_resp') or {}).get('status_code', 0) or result.get('input_sensitive'):
                        raise ValueError('MiniMax did not approve this voice clone. Check your provider account.')
                    provider_note = 'MiniMax may delete a clone that is not used within 7 days. No preview text was sent during cloning.'
        fresh = self.channel_voice(pid, vid)
        fresh['providers'][provider] = {'id': voice_id, 'requires_verification': verification,
                                        'created': time.time(), 'note': provider_note}
        self.save_channel_voice(pid, fresh)
        job['result'] = {'voice_id': voice_id, 'channel_voice_id': vid, 'provider': provider,
                         'requires_verification': verification, 'note': provider_note}
