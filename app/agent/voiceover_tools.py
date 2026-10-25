"""The dedicated Voiceovers workspace exposed equally to Claude and Codex."""
import asyncio
import json


def tools(studio):
    from .studio import Tool
    service, pid = studio.voice_studio, studio.pid
    speaker = {'type': 'object', 'properties': {
        'id': {'type': 'string'}, 'label': {'type': 'string'},
        'provider': {'type': 'string', 'enum': ['chatterbox', 'elevenlabs', 'minimax']},
        'voice_id': {'type': 'string'}, 'channel_voice_id': {'type': 'string', 'description': 'Saved permitted identity such as Peter/Miles; resolves its prepared provider voice.'},
        'use_clone_settings': {'type': 'boolean', 'default': True, 'description': 'Use the saved best clone-comparison model/native settings. False allows explicit native settings instead.'},
        'reference_speaker': {'type': 'string'},
        'wpm': {'type': 'number', 'minimum': 60, 'maximum': 350},
        'gap_ms': {'type': 'integer', 'minimum': 0, 'maximum': 5000},
        'pause_ms': {'type': 'integer', 'minimum': 0, 'maximum': 2000},
        'effect': {'type': 'string', 'enum': ['clean', 'radio', 'miles_radio']},
        'model': {'type': 'string'}, 'speed': {'type': 'number', 'minimum': .5, 'maximum': 2},
        'use_target_wpm': {'type': 'boolean'},
        'stability': {'type': 'number', 'minimum': 0, 'maximum': 1},
        'similarity': {'type': 'number', 'minimum': 0, 'maximum': 1},
        'style': {'type': 'number', 'minimum': 0, 'maximum': 1},
        'pitch': {'type': 'integer', 'minimum': -12, 'maximum': 12},
        'volume': {'type': 'number', 'minimum': .1, 'maximum': 3}},
        'required': ['id', 'provider']}
    generation = {'type': 'object', 'properties': {
        'use_reviewed_plan': {'type': 'boolean', 'description': 'Use true on its own to reuse the exact script and settings last saved by Review in this project. Preserves preview/full mode. Omit other generation fields.'},
        'file': {'type': 'string'}, 'text': {'type': 'string'}, 'name': {'type': 'string'},
        'format': {'type': 'string', 'enum': ['shorts', 'longform']},
        'reference_id': {'type': 'string'}, 'preview': {'type': 'boolean'},
        'episode_number': {'type': 'string'}, 'consistent_pauses': {'type': 'boolean'},
        'pause_ms': {'type': 'integer', 'minimum': 0, 'maximum': 2000},
        'turn_gap_ms': {'type': 'integer', 'minimum': 0, 'maximum': 5000},
        'export_stems': {'type': 'boolean'},
        'speakers': {'type': 'array', 'items': speaker, 'minItems': 1, 'maxItems': 2}}}

    async def status(_):
        refs = [{'id': r['id'], 'name': r['name'], 'kind': r['kind'], 'seconds': r['seconds'],
                 'analysis': {k: v for k, v in (r.get('analysis') or {}).items()
                              if k in ('speakers', 'pauses', 'wpm', 'notes', 'language', 'overlapping_words')}}
                for r in service.references(pid)]
        return json.dumps({'page': '/voiceovers', 'providers': service.provider_status(), 'references': refs,
                           'channel_voices': service.channel_voices(pid),
                           'last_reviewed_plan': service.last_reviewed_plan(pid),
                           'jobs': [{k: j.get(k) for k in ('id', 'kind', 'status', 'name', 'files', 'voice_job_id', 'error')}
                                    for j in service.jobs(pid)]})

    async def voices(args):
        return json.dumps(await service.voices(args['provider']))

    async def analyze(args):
        return json.dumps(service.start_analysis(pid, args['reference_id'], args.get('expected_speakers', 2),
                                                 args.get('language', ''), approved=True))

    async def plan(args):
        return json.dumps(service.plan(pid, args))

    async def generate(args):
        return json.dumps(service.start_generation(pid, args, approved=True))

    async def prepare(args):
        return json.dumps(service.prepare_channel_voice(pid, args['channel_voice_id'], args['provider'], approved=True))

    async def repair(args):
        return json.dumps(service.start_repair(pid, args['job_id'], args['sections'], args.get('reason', ''), approved=True))

    async def clone_check(args):
        return json.dumps(service.start_clone_check(pid, args['channel_voice_id'], args.get('provider', 'chatterbox'),
                          args.get('test_text', ''), args.get('attempts', 5), approved=True,
                          auto_retry=args.get('auto_retry', True), target_similarity=args.get('target_similarity')))

    async def save_voice(args):
        return json.dumps(await service.import_channel_voice(pid, args['name'], args['file'], args.get('consent', False)))

    async def job(args):
        until = asyncio.get_running_loop().time() + max(0, min(30, float(args.get('wait_seconds') or 0)))
        while True:
            result = service.job(pid, args['job_id'])
            if result['status'] not in ('queued', 'running') or asyncio.get_running_loop().time() >= until:
                return json.dumps(result)
            await asyncio.sleep(.5)

    return [
        Tool('voiceover_studio_status', 'Inspect Voiceovers connections, learned profiles and jobs without spending.',
             {'type': 'object', 'properties': {}}, status),
        Tool('voiceover_provider_voices', 'List configured provider voices without generating audio.',
             {'type': 'object', 'properties': {'provider': {'type': 'string', 'enum': ['chatterbox', 'elevenlabs', 'minimax']}},
              'required': ['provider']}, voices),
        Tool('voiceover_reference_analyze', 'Analyze an uploaded reference with ElevenLabs Scribe: speakers, WPM, gaps and possible radio processing. Sends audio out and uses credits; asks every time.',
             {'type': 'object', 'properties': {'reference_id': {'type': 'string'},
                                               'expected_speakers': {'type': 'integer', 'minimum': 1, 'maximum': 2},
                                               'language': {'type': 'string'}}, 'required': ['reference_id']}, analyze),
        Tool('voiceover_studio_plan', 'Validate script and speaker mapping before spending. Speaker labels assign voices and are never spoken. Use use_reviewed_plan=true alone for the last UI review.', generation, plan),
        Tool('voiceover_studio_generate', 'Generate both speakers and assemble the final with their effects, silence editing and mastering. Requires fresh approval. Use plan first. use_reviewed_plan=true alone reuses the last UI review including preview/full mode.', generation, generate),
        Tool('voiceover_prepare_voice', 'Prepare a saved permitted clean channel voice for Chatterbox, ElevenLabs or MiniMax. Radio samples are never used for identity cloning. Cloud cloning uploads audio and may use credits/voice slots; fresh approval required.',
             {'type': 'object', 'properties': {'channel_voice_id': {'type': 'string'},
                                               'provider': {'type': 'string', 'enum': ['chatterbox', 'elevenlabs', 'minimax']}},
              'required': ['channel_voice_id', 'provider']}, prepare),
        Tool('voiceover_save_voice', 'Save an uploaded project audio file as a named clean channel voice (for example Peter or Miles), so clone-and-check can use it. Local import only; no GPU or provider call. Requires explicit permission to clone this single-speaker source; competitor and paired radio files cannot be imported as identity. Reuse an existing named voice when already saved.',
             {'type': 'object', 'properties': {'file': {'type': 'string'}, 'name': {'type': 'string', 'maxLength': 40},
                'consent': {'type': 'boolean', 'description': 'True only when the user has confirmed permission to clone this clean voice.'}},
              'required': ['file', 'name', 'consent']}, save_voice),
        Tool('voiceover_clone_and_check', 'Clone/reuse a permitted clean voice, then automatically generate, compare, refine supported controls and retain the best eligible take/settings. Default Chatterbox; use another supported provider only when requested. Default cap 5, maximum 10 takes per fresh approval. Stops when a supplied calibrated identity target is reached, two attempts show no meaningful progress, the scorer is unavailable or the cap is reached. Never invent a target or call cosine a percent/perfect match. Inspect results and request listening review; do not chain new jobs past the approved cap.',
             {'type': 'object', 'properties': {'channel_voice_id': {'type': 'string'},
                'provider': {'type': 'string', 'enum': ['chatterbox', 'elevenlabs', 'minimax'], 'default': 'chatterbox'},
                'test_text': {'type': 'string', 'maxLength': 600},
                'attempts': {'type': 'integer', 'minimum': 1, 'maximum': 10, 'default': 5},
                'auto_retry': {'type': 'boolean', 'default': True},
                'target_similarity': {'type': 'number', 'minimum': 0, 'maximum': 1, 'description': 'Optional cosine target explicitly calibrated/approved using the owner’s reference examples. Omit if uncalibrated; never invent a perfect-match threshold.'}},
              'required': ['channel_voice_id']}, clone_check),
        Tool('voiceover_studio_repair', 'Regenerate only selected 1-based section numbers in a completed take; reuse all other source audio, reassemble and remaster a new take, preserving the original. Inspect sections through voiceover_studio_job. Requires fresh approval, not automatic listening certification. Log the issue in reason; listen again after repair.',
             {'type': 'object', 'properties': {'job_id': {'type': 'string'},
                'sections': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}, 'minItems': 1, 'maxItems': 20},
                'reason': {'type': 'string', 'maxLength': 1000}}, 'required': ['job_id', 'sections']}, repair),
        Tool('voiceover_studio_job', 'Read a Voiceovers job, optionally waiting up to 30 seconds. Report review flags and estimated subtitle timing honestly.',
             {'type': 'object', 'properties': {'job_id': {'type': 'string'}, 'wait_seconds': {'type': 'number'}},
              'required': ['job_id']}, job),
    ]
