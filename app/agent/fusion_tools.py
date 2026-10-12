"""Fusion episode tools for the agents: fusion_setup (the fixed runner builds the Resolve project)
and fusion_stills (review stills). The shots themselves are scripted through Resolve's MCP."""
import asyncio
import json
import logging

from ..fusion import FusionError

log = logging.getLogger("oneprod.fusion")


def tools(studio):
    from .studio import Tool
    fusion, pid = studio.fusion, studio.pid

    async def setup(args):
        try:
            return json.dumps(await asyncio.to_thread(
                fusion.setup, pid, str(args.get('shots') or ''), str(args.get('art') or ''), str(args.get('voiceover') or ''),
                str(args.get('subtitles') or ''), str(args.get('music') or ''), str(args.get('name') or 'Episode'),
                str(args.get('sfx') or '')))
        except FusionError as exc:
            # logged: the first real draft lost its only footage shot after a fusion_setup error nobody saw (2026-10-12)
            log.warning("fusion_setup refused in %s: %s", pid, exc)
            return json.dumps({'error': str(exc)})

    async def stills(args):
        try:
            return json.dumps(await asyncio.to_thread(fusion.stills, pid, str(args.get('state') or '')))
        except FusionError as exc:
            return json.dumps({'error': str(exc)})

    return [Tool('fusion_setup', 'Build a NEW DaVinci Resolve project (1080p 60 fps) for an episode: the voiceover on A1, '
                 'music on A2, and one empty Fusion composition per shot, cut at the frames where each shot\'s anchor '
                 'words are spoken. art.json = the art direction {"concept", "palette": {"bg", "ink", "accent", ...: '
                 '"#RRGGBB"}, "fonts": {"heading", "body", "mono"}, "motion", "three_d"}; shots.json = {"shots": '
                 '[{"idea", "text"}, {"anchor": "exact words from the subtitles", "idea", "text"}, ...]}. Returns each '
                 'shot\'s name (shot-001 ...) and length, and the state file for fusion_stills. Then script every shot '
                 'with mcp__davinci__run_script using the Fusion kit. A shot may carry "stock": "stock/<file>.mp4" + '
                 '"from" (seconds) to sit on Pexels footage. sfx = optional sfx.json [{"file": "sfx/...", "shot": '
                 '"shot-003" (on that cut) or "anchor": "words", "offset_s", "gain_db" (default -18)}], placed on A3.',
                 {'type': 'object', 'properties': {
                     'shots': {'type': 'string'}, 'art': {'type': 'string'}, 'voiceover': {'type': 'string'},
                     'subtitles': {'type': 'string'}, 'music': {'type': 'string'}, 'name': {'type': 'string'},
                     'sfx': {'type': 'string'}},
                  'required': ['shots', 'art', 'voiceover', 'subtitles']}, setup),
            Tool('fusion_stills', 'Export review stills (middle and end of every shot) of the episode\'s Resolve timeline into '
                 '<folder>/stills/ and list shots that are still empty. state = the fusion.json fusion_setup wrote.',
                 {'type': 'object', 'properties': {'state': {'type': 'string'}}, 'required': ['state']}, stills)]
