"""Sound-effect tools for Claude, Codex and team workers: search Epidemic Sound, fetch into the project.
The owner's key stays in Credential Manager; the agent only sees results and file names."""
import asyncio
import json

from ..sfx import SfxError


def tools(studio):
    from .studio import Tool
    sfx, pid = studio.sfx, studio.pid

    async def search(args):
        try:
            return json.dumps(await asyncio.to_thread(sfx.search, str(args.get('term') or ''), int(args.get('count') or 10),
                                                      float(args.get('max_seconds') or 0)))
        except (SfxError, ValueError) as exc:
            return json.dumps({'error': str(exc)})

    async def fetch(args):
        try:
            return json.dumps(await asyncio.to_thread(sfx.fetch, pid, str(args.get('id') or ''), str(args.get('title') or '')))
        except SfxError as exc:
            return json.dumps({'error': str(exc)})

    return [Tool('sfx_search', 'Search Epidemic Sound sound effects (the owner\'s plan). Returns id, title, seconds and whether '
                 'it is already downloaded. Downloads count against the owner\'s plan: prefer effects marked cached, and '
                 'reuse one effect for every similar moment instead of fetching many.',
                 {'type': 'object', 'properties': {
                     'term': {'type': 'string', 'description': 'e.g. whoosh, soft click, pop, riser, impact, paper, cash register'},
                     'count': {'type': 'integer', 'description': '1-60, default 10'},
                     'max_seconds': {'type': 'number', 'description': 'only effects at most this long'}},
                  'required': ['term']}, search),
            Tool('sfx_fetch', 'Put one Epidemic Sound effect into this project as sfx/epidemic-<id>.mp3 (downloaded once, then '
                 'reused) and credit it in sfx/CREDITS.md. Place it with sfx.json: [{"file": "sfx/...", "anchor": "words from '
                 'the subtitles", "offset_s": seconds after them (may be negative), "gain_db": -18}].',
                 {'type': 'object', 'properties': {'id': {'type': 'string'}, 'title': {'type': 'string'}},
                  'required': ['id']}, fetch)]
