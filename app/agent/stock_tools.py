"""Stock footage tools for Claude, Codex and team workers: search Pexels, fetch into the project.
The owner's key stays in Credential Manager; the agent only sees results and file names."""
import asyncio
import json

from ..stock import StockError


def tools(studio):
    from .studio import Tool
    stock, pid = studio.stock, studio.pid

    async def search(args):
        try:
            return json.dumps(await asyncio.to_thread(
                stock.search, str(args.get('query') or ''), str(args.get('kind') or 'video'),
                str(args.get('orientation') or 'landscape'), float(args.get('min_seconds') or 0),
                int(args.get('count') or 10), int(args.get('page') or 1)))
        except (StockError, ValueError) as exc:
            return json.dumps({'error': str(exc)})

    async def fetch(args):
        try:
            return json.dumps(await asyncio.to_thread(stock.fetch, pid, str(args.get('kind') or 'video'), args.get('id')))
        except StockError as exc:
            return json.dumps({'error': str(exc)})

    return [Tool('stock_search', 'Search free Pexels stock videos or photos (the owner\'s key). Returns id, what it shows '
                 '("about"), seconds (videos), size, photographer and page. Only 720p-1080p mp4s and photos at least '
                 '720 px are listed. Searches count against the owner\'s Pexels limit - search with specific words, '
                 'not once per second of video.',
                 {'type': 'object', 'properties': {
                     'query': {'type': 'string', 'description': 'what should be in the shot, in English'},
                     'kind': {'type': 'string', 'enum': ['video', 'photo']},
                     'orientation': {'type': 'string', 'enum': ['landscape', 'portrait', 'square']},
                     'min_seconds': {'type': 'number', 'description': 'videos at least this long'},
                     'count': {'type': 'integer', 'description': '1-40, default 10'},
                     'page': {'type': 'integer'}},
                  'required': ['query']}, search),
            Tool('stock_fetch', 'Download one Pexels video or photo by its id into this project as stock/pexels-<kind>-<id>.mp4/.jpg '
                 '(once; asking again reuses the file) and add its credit to stock/CREDITS.md. Use the file in shots JSON '
                 '({"file": "stock/...", "anchor": ..., "from": seconds-into-the-clip}) or an edit list.',
                 {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['video', 'photo']},
                                                   'id': {'type': 'integer'}},
                  'required': ['kind', 'id']}, fetch)]
