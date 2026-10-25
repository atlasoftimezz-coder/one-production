"""Bounded clone + clean preview + local speaker comparison, shared by UI/agents."""
import asyncio
import json
import math
import re
import secrets
import time

from . import media
from .storage import safe_filename

DEFAULT_TEST_TEXT = ('I want to tell you something that surprised me. At first, everything seemed normal. '
                     'Then I heard a familiar voice, paused for a moment, and asked what had happened.')
CHATTERBOX_TRIALS = [(.65, .40, .80), (.50, .50, .80), (.50, .30, .80), (.40, .50, .80),
                    (.65, .30, .85), (.80, .30, .85), (.80, .50, .80)]


def comparison_settings(provider, attempt, previous=None):
    """Bounded search over supported controls; no new clone identities are created by retries."""
    if attempt == 0 and previous:
        settings = dict(previous)
        if provider == 'chatterbox':
            settings.setdefault('speed', 1)
        return settings
    if provider == 'chatterbox':
        choices = [dict(zip(('exaggeration', 'cfg_weight', 'temperature'), row)) for row in CHATTERBOX_TRIALS]
        if previous:
            choices = [s for s in choices if any(s[k] != previous.get(k) for k in s)]
        settings = dict(choices[(attempt - bool(previous)) % len(choices)])
        settings['speed'] = 1
        settings['seed'] = 4242 if attempt == 0 else 4243 + secrets.randbelow(2**31 - 4243)
        return settings
    if provider == 'elevenlabs':
        choices = [{'speed': 1, 'similarity': s, 'stability': v, 'style': 0}
                   for s, v in ((.75, .5), (.9, .5), (1, .5), (.9, .35), (.9, .65))]
        if previous:
            choices = [s for s in choices if any(s[k] != previous.get(k) for k in s)]
        return dict(choices[(attempt - bool(previous)) % len(choices)])
    return {'speed': 1, 'pitch': 0, 'volume': 1}


class CloneChecks:
    def start_clone_check(self, pid, vid, provider='chatterbox', test_text='', attempts=5, approved=False,
                          auto_retry=True, target_similarity=None):
        if not approved:
            raise ValueError('Approve this clone and comparison. It can upload the clean reference, create a provider clone and generate the requested number of test takes.')
        if provider not in ('chatterbox', 'elevenlabs', 'minimax'):
            raise ValueError('Choose Chatterbox, ElevenLabs or MiniMax.')
        if type(attempts) is not int or not 1 <= attempts <= 10:
            raise ValueError('Choose 1–10 comparison takes; each take can use provider credits.')
        if type(auto_retry) is not bool:
            raise ValueError('Choose whether automatic refinement is enabled.')
        if target_similarity is not None and (type(target_similarity) not in (int, float) or
                not math.isfinite(target_similarity) or not 0 <= target_similarity <= 1):
            raise ValueError('Use a calibrated target cosine between 0 and 1, or leave it unset.')
        text = str(test_text or DEFAULT_TEST_TEXT).strip()
        if not 20 <= len(text) <= 600 or len(text.split()) < 5:
            raise ValueError('Use a comparison passage of 20–600 characters and at least five words.')
        if any(marker in text for marker in ('<#', '\n', '\r')) or text.startswith(('#', '[')) or re.match(r'^[\w][\w -]{0,39}:', text):
            raise ValueError('Use plain spoken text for this single-voice test, without speaker labels or pause tags.')
        voice = self.channel_voice(pid, vid)
        if not voice.get('consent'):
            raise ValueError('Only permitted clean channel voices can be cloned and compared.')
        if provider != 'chatterbox' and not self.key(provider):
            raise ValueError('Add this provider’s API key under Connections first.')
        if (voice['providers'].get(provider) or {}).get('requires_verification'):
            raise ValueError('Complete the provider’s voice verification before generating a comparison. Verification cannot be bypassed.')
        self.ffmpeg()
        return self.launch(pid, 'voice_clone_check',
            lambda job: self._clone_check(pid, vid, provider, text, attempts, job, auto_retry, target_similarity),
            name=f"{voice['name']} · {provider} clone comparison", channel_voice_id=vid,
            provider=provider, test_text=text, max_takes=attempts, auto_retry=bool(auto_retry), target_similarity=target_similarity)

    async def _clone_check(self, pid, vid, provider, text, attempts, job, auto_retry, target_similarity):
        from .voice_studio import write_json, DEFAULT_MODELS
        voice = self.channel_voice(pid, vid)
        reused = provider in voice['providers']
        if not reused:
            await self._prepare_channel_voice(pid, vid, provider, job)
            voice = self.channel_voice(pid, vid)
        binding = voice['providers'][provider]
        if binding.get('requires_verification'):
            job.update(result={**job.get('result', {}), 'status': 'provider_verification_required',
                'review_status': 'not_checked', 'automatically_accepted': False},
                flags=['Complete the provider’s voice verification, then run Clone & check again. No test speech was generated.'])
            return
        ff = self.ffmpeg()
        work = self.root(pid) / 'work' / job['id']
        work.mkdir(parents=True, exist_ok=True)
        reference = work / 'reference-for-comparison.wav'
        result = await asyncio.to_thread(media.run, [ff, '-y', '-v', 'error', '-i',
            str(self.store.file_path(pid, voice['clean_file'])), '-t', '60', '-ac', '1', '-ar', '16000',
            '-c:a', 'pcm_s16le', str(reference)], 120)
        if result.returncode:
            raise ValueError('Could not decode the clean sample for comparison.')
        candidates, flags = [], []
        best, progress_score, stale = None, None, 0
        stop_reason = 'attempt_limit'
        for i in range(attempts):
            job.update(step=f'Generating clean comparison take {i + 1}/{attempts}', completed=i, total=attempts)
            self.save_job(job)
            raw = work / f'comparison-{i}.audio'
            settings = comparison_settings(provider, i, binding.get('generation_settings')) if auto_retry else comparison_settings(provider, 0, binding.get('generation_settings'))
            if not auto_retry and provider == 'chatterbox' and i:
                settings['seed'] = 4243 + secrets.randbelow(2**31 - 4243)
            if provider != 'chatterbox':
                settings['model'] = (binding.get('generation_settings') or {}).get('model') or self.config().get(provider, {}).get('model') or DEFAULT_MODELS[provider]
            speaker = {'id': 'A', 'label': voice['name'], 'provider': provider, 'voice_id': binding['id'],
                       'speed': 1, **settings}
            if provider == 'chatterbox':
                speaker['chatterbox_settings'] = {k: settings[k] for k in ('exaggeration', 'cfg_weight', 'temperature')}
            await self.synthesize(speaker, text, raw)
            target = self.store.unique_path(pid, safe_filename(f"{voice['name']} {provider} Clone Check {job['id']} Take {i + 1}.wav"))
            result = await asyncio.to_thread(media.run, [ff, '-y', '-v', 'error', '-i', str(raw),
                '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s16le', str(target)], 120)
            if result.returncode:
                raise ValueError('Could not export the comparison audio.')
            # Save immediately so completed, possibly billed takes survive a later failure.
            job['files'].append(target.name)
            self.save_job(job)
            job['step'] = 'Comparing clean voice identity with the coworker’s trained speaker encoder'
            check = None
            try:
                check = await self.service.compare_voice(reference, target)
                score = check.get('speaker_cosine')
                if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score) or not -1 <= score <= 1:
                    raise ValueError('The speaker comparison returned no valid cosine score.')
                # A scorer cannot silently turn evidence into production acceptance.
                check.update(status='needs_listening_review', automatically_accepted=False)
            except Exception as exc:
                from .security.redact import redact
                flags.append(f'Take {i + 1}: automatic speaker comparison unavailable. {redact(str(exc))[:300]}')
                check = {'status': 'unavailable', 'speaker_cosine': None, 'automatically_accepted': False}
            native = raw.with_suffix('.qc.json')
            transcript = json.loads(native.read_text(encoding='utf-8')) if native.is_file() else None
            known_misread = any(s.get('status') in ('check', 'failed') for s in (transcript or {}).get('sections', []))
            candidates.append({'take': i + 1, 'file': target.name, 'comparison': check,
                'transcript_check': transcript, 'known_misread': known_misread, 'generation_settings': settings})
            score = check.get('speaker_cosine')
            eligible = score is not None and not known_misread
            if eligible and (best is None or score > best['comparison']['speaker_cosine']):
                best = candidates[-1]
            if eligible and (progress_score is None or score >= progress_score + .005):
                progress_score, stale = score, 0
            else:
                stale += 1
            job['result'] = {'channel_voice_id': vid, 'provider': provider, 'candidates': candidates,
                             'recommended_take': best['take'] if best else None,
                             'review_status': 'needs_listening_review', 'automatically_accepted': False}
            self.save_job(job)
            if check['status'] == 'unavailable':
                flags.append('Stopped after this take because the scorer is unavailable; no further test synthesis was requested.')
                stop_reason = 'scorer_unavailable'
                break
            if auto_retry and best and target_similarity is not None and best['comparison']['speaker_cosine'] >= target_similarity:
                stop_reason = 'identity_target_reached'
                break
            if auto_retry and stale >= 2:
                stop_reason = 'no_improvement'
                break
        if target_similarity is None:
            flags.append('No calibrated identity target was supplied. Retrying can rank takes but cannot establish a perfect match.')
        if stop_reason == 'no_improvement':
            flags.append('Stopped after two takes without meaningful eligible-score improvement. Review the best take or improve the source before another approved search.')
        if stop_reason == 'attempt_limit':
            flags.append('The approved take limit was reached. The best eligible take is retained; further generation requires another approved request.')
        if any(c['known_misread'] for c in candidates):
            flags.append('Takes flagged by Chatterbox’s transcript check are excluded from the recommendation and saved production settings.')
        target_reached = bool(best and target_similarity is not None and best['comparison']['speaker_cosine'] >= target_similarity)
        report = {'channel_voice_id': vid, 'voice_name': voice['name'], 'provider': provider,
                  'voice_id': binding['id'], 'reused_existing_clone': reused, 'reference_file': voice['clean_file'],
                  'test_text': text, 'candidates': candidates, 'recommended_take': best['take'] if best else None,
                  'auto_retry': bool(auto_retry), 'stop_reason': stop_reason, 'max_takes': attempts,
                  'identity_target': target_similarity, 'identity_target_reached': target_reached,
                  'minimum_progress': .005, 'patience': 2,
                  'review_status': 'needs_listening_review', 'automatically_accepted': False,
                  'acceptance_threshold': None, 'flags': flags,
                  'note': 'Speaker cosine is ranking evidence, not a percentage match. Listen to clean reference and test audio. Pronunciation, emotion and artifacts need separate review. No radio effect was applied.'}
        path = self.store.unique_path(pid, safe_filename(f"{voice['name']} {provider} Clone Check {job['id']}-report.json"))
        write_json(path, report)
        job.update(result=report, files=[*job['files'], path.name], flags=flags, completed=len(candidates), total=attempts)
        fresh = self.channel_voice(pid, vid)
        fresh['providers'][provider]['latest_check'] = {'job_id': job['id'], 'created': time.time(),
            'status': 'needs_listening_review', 'recommended_take': best['take'] if best else None,
            'stop_reason': stop_reason, 'identity_target_reached': target_reached,
            'speaker_cosine': best['comparison']['speaker_cosine'] if best else None}
        if best:
            fresh['providers'][provider]['generation_settings'] = best['generation_settings']
            fresh['providers'][provider]['generation_settings_source'] = {'job_id': job['id'], 'take': best['take'],
                'speaker_cosine': best['comparison']['speaker_cosine'], 'provisional': True}
        self.save_channel_voice(pid, fresh)
