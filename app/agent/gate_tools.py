"""quality_gate: the numeric stage gates for Claude, Codex and team workers (measures only)."""
import json

from ..gates_api import PackageCheck, VideoCheck, VoiceCheck


def tools(studio):
    from .studio import Tool
    gates, pid = studio.gates, studio.pid

    async def run(args):
        stage = args.get('stage')
        try:
            if stage == 'voice':
                report = await gates.voice(pid, VoiceCheck(**{k: str(args.get(k) or '') for k in ('job_id', 'audio', 'srt', 'voice', 'reference_file')}))
            elif stage == 'video':
                report = await gates.video(pid, VideoCheck(video=str(args.get('video') or ''), voiceover=str(args.get('voiceover') or '')))
            elif stage == 'package':
                report = await gates.package(pid, PackageCheck(meta_file=str(args.get('meta_file') or ''), video=str(args.get('video') or ''),
                                                               thumbnail=str(args.get('thumbnail') or '')))
            else:
                return json.dumps({'error': 'stage must be voice, video or package'})
        except Exception as exc:                        # noqa: BLE001 - reported to the agent
            return json.dumps({'error': str(exc)})
        return json.dumps({k: report[k] for k in ('stage', 'passed', 'summary', 'checks', 'report')})

    return [Tool('quality_gate', 'Measure a finished stage against fixed limits. voice: job_id (or audio + srt + voice/reference_file); '
                 'video: video + voiceover; package: meta_file (JSON with title, description, tags, chapters [{time, title}]) + video '
                 '+ thumbnail. Saves a report under gates/.',
                 {'type': 'object', 'properties': {
                     'stage': {'type': 'string', 'enum': ['voice', 'video', 'package']},
                     **{k: {'type': 'string'} for k in ('job_id', 'audio', 'srt', 'voice', 'reference_file', 'video', 'voiceover',
                                                       'meta_file', 'thumbnail')}},
                  'required': ['stage']}, run)]
