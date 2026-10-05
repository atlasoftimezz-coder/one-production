"""Resolve tools for Claude, Codex and team workers: look and propose only. Executing is the owner's."""
import asyncio
import json

from ..resolve import MIN_VERSION, NOT_SUPPORTED, OPERATIONS, PlanError


def tools(studio):
    from .studio import Tool
    resolve, pid = studio.resolve, studio.pid

    async def status(args):
        live = await asyncio.to_thread(resolve.probe)
        return json.dumps({'enabled': resolve.enabled, 'installed': live, 'min_version': MIN_VERSION,
                           'manual_version_set_by_owner': studio.knowledge.context(pid)['version'] if studio.knowledge else '',
                           'operations': OPERATIONS, 'not_supported': NOT_SUPPORTED,
                           'rule': 'You can only propose a plan. The owner reviews and executes it on a NEW disposable project.'})

    async def propose(args):
        installed = await asyncio.to_thread(resolve.installed_version, pid)
        try:
            record = await asyncio.to_thread(resolve.propose, pid, args.get('plan') or {}, 'worker', installed)
        except PlanError as exc:
            return json.dumps({'error': str(exc)})
        return json.dumps({'plan_id': record['id'], 'status': 'waiting for the owner', 'warnings': record['warnings'],
                           'version_checked': record['version_checked'],
                           'note': 'Tell the owner to open the Resolve panel to review and run it. Do not claim it ran.'})

    plan_schema = {'type': 'object', 'properties': {
        'title': {'type': 'string'},
        'requires_version': {'type': 'string', 'description': 'lowest Resolve version the cited procedure needs'},
        'settings': {'type': 'object', 'description': 'timelineFrameRate, timelineResolutionWidth, timelineResolutionHeight'},
        'operations': {'type': 'array', 'items': {'type': 'object'},
                       'description': 'ordered steps: {"op":"import_media","files":[...]}, {"op":"create_timeline","name":...}, '
                                      '{"op":"append_clips","files":[...]}, {"op":"add_marker","frame":0,"color":"Blue","name":...,"note":...,"duration":1}'},
        'citations': {'type': 'array', 'items': {'type': 'object'}, 'description': 'knowledge_search citations supporting the plan'}},
        'required': ['title', 'operations']}
    return [Tool('resolve_status', 'Installed DaVinci Resolve version, whether Resolve control is on, and the only operations a plan may use.',
                 {'type': 'object', 'properties': {}}, status),
            Tool('resolve_propose_plan', 'Propose a Resolve edit plan using only the supported operations and media files in this project. '
                 'It is validated and saved for the owner to review; it never runs by itself and only ever builds a new project.',
                 {'type': 'object', 'properties': {'plan': plan_schema}, 'required': ['plan']}, propose)]
