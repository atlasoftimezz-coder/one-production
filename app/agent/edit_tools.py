"""ffmpeg editor tools for the agents: render_edit (heavy-job gated by the policy) and edit_status."""
import asyncio
import json

from ..editor import EditError
from ..media import MediaError

EDIT_FORMAT = ('Edit list JSON in this folder: {"voiceover": "vo.wav", "size": "1920x1080", "fps": 30, "output": "episode.mp4", '
               '"visuals": [{"file": "art/01.png", "seconds": 3.5, "motion": "push|pull|pan_left|pan_right|none"}, '
               '{"file": "clip.mp4", "seconds": 4, "from": 12.0}, {"card": {"text": "1986", "style": "title|date|label"}, "seconds": 2}], '
               '"music": {"file": "bed.mp3", "db": -22, "duck": true}, "subtitles": {"file": "vo.srt", "burn": false}}. '
               'Visuals play back to back (hard cuts) and must add up to the voiceover length within 2 s; the last one is fitted.')


def tools(studio):
    from .studio import Tool
    editor, pid = studio.editor, studio.pid

    async def render(args):
        try:
            job = await asyncio.to_thread(editor.start, pid, str(args.get('edit') or ''))
        except (EditError, MediaError) as exc:
            return json.dumps({'error': str(exc)})
        return json.dumps({'job_id': job['id'], 'status': job['status'], 'visuals': job['visuals'],
                           'fitted_by_seconds': job['fitted_by'], 'note': 'Follow it with edit_status.'})

    async def status(args):
        wait = min(float(args.get('wait_seconds') or 0), 900)
        loop = asyncio.get_running_loop()
        end = loop.time() + wait
        while True:
            try:
                job = editor.get(pid, str(args.get('job_id') or ''))
            except EditError as exc:
                return json.dumps({'error': str(exc)})
            if job['status'] != 'rendering' or loop.time() >= end:
                break
            await asyncio.sleep(2)
        gate = job.get('gate') or {}
        return json.dumps({k: job[k] for k in ('status', 'step', 'progress', 'output', 'error')} |
                          {'video_gate': {'passed': gate.get('passed'), 'summary': gate.get('summary'), 'report': gate.get('report')} if gate else None})

    async def build(args):
        if not studio.animation:
            return json.dumps({'error': 'Animation is not available.'})
        try:
            return json.dumps(await asyncio.to_thread(studio.animation.build, pid, str(args.get('shots') or ''), str(args.get('edit') or '')))
        except (EditError, MediaError) as exc:
            return json.dumps({'error': str(exc)})

    return [Tool('build_animation', 'Render the SVG drawings listed in a shots JSON to frames, time each shot to the voiceover '
                 '(first shot at 0, the others when their anchor words are spoken, found in the subtitles in order) and write '
                 'the edit list for render_edit. Shots JSON: {"voiceover": "vo.wav", "subtitles": "vo.srt", "size": "1920x1080", '
                 '"fps": 30, "output": "episode.mp4", "burn_subtitles": false, "music": {...}, "shots": [{"svg": "art/s01.svg", '
                 '"motion": "push"}, {"svg": "art/s02.svg", "anchor": "exact words from the script", "motion": "pan_right"}, '
                 '{"card": {"text": "1986", "style": "date"}, "anchor": "..."}]}. Each shot needs >= 0.4 s.',
                 {'type': 'object', 'properties': {'shots': {'type': 'string'}, 'edit': {'type': 'string', 'description': 'edit list name (optional)'}},
                  'required': ['shots']}, build),
            Tool('render_edit', 'Render a finished video from an edit list with ffmpeg on the processor (the owner approves it first). ' + EDIT_FORMAT,
                 {'type': 'object', 'properties': {'edit': {'type': 'string', 'description': 'edit list JSON file in this project'}},
                  'required': ['edit']}, render),
            Tool('edit_status', 'Progress or result of a render, including its video gate. wait_seconds (max 900) blocks until it finishes.',
                 {'type': 'object', 'properties': {'job_id': {'type': 'string'}, 'wait_seconds': {'type': 'number'}},
                  'required': ['job_id']}, status)]
