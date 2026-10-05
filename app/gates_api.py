"""Protected routes for the stage gates. Reports are saved in the project under gates/."""
import asyncio
import json
import time
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import gates, media
from .storage import NotFound


class VoiceCheck(BaseModel):
    job_id: str = ''
    audio: str = ''
    srt: str = ''
    voice: str = Field(default='', description='reference voice from the voice app')
    reference_file: str = Field(default='', description='or a reference clip in this project')


class VideoCheck(BaseModel):
    video: str
    voiceover: str


class PackageCheck(BaseModel):
    meta: dict | None = None
    meta_file: str = ''
    video: str = ''
    thumbnail: str = ''


class Gates:
    """Shared by the routes and the agents' quality_gate tool."""

    def __init__(self, store, data_dir: Path, voice_svc, voice_jobs):
        self.store, self.data_dir, self.voice_svc, self.voice_jobs = store, data_dir, voice_svc, voice_jobs

    def ffmpeg(self) -> str:
        found = media.find_ffmpeg(self.data_dir)
        if not found:
            raise ValueError('ffmpeg was not found. Install it from Setup (free) or put it on PATH.')
        return found

    def file(self, pid: str, name: str) -> Path:
        try:
            return self.store.file_path(pid, name)
        except NotFound:
            raise ValueError(f'{name} is not a file in this project.') from None

    async def reference(self, voice: str) -> Path | None:
        try:
            meta = await self.voice_svc.meta()
        except Exception:                                  # noqa: BLE001 - voice app not running
            return None
        folder = Path(meta.get('voices_dir') or '.').resolve()
        path = (folder / voice).resolve()
        return path if voice in meta.get('voices', []) and path.parent == folder and path.is_file() else None

    async def voice(self, pid: str, body: VoiceCheck) -> dict:
        ff = self.ffmpeg()
        sections, required, srt = None, False, None
        if body.job_id:
            job = self.voice_jobs.get(body.job_id)
            if not job or job.get('project') != pid:
                raise ValueError('No such voiceover in this project.')
            if job['status'] != 'done':
                raise ValueError(f"That voiceover is {job['status']}, not finished.")
            audio = self.file(pid, next(f for f in job['files'] if f.lower().endswith('.wav')))
            srt = next((self.file(pid, f) for f in job['files'] if f.lower().endswith('.srt')), None)
            sections, required = job.get('sections') or [], True
            reference = await self.reference(job['voice'])
            source = {'job': job['id'], 'voice': job['voice']}
        else:
            if not body.audio:
                raise ValueError('Choose a voiceover job or an audio file.')
            audio = self.file(pid, body.audio)
            srt = self.file(pid, body.srt) if body.srt else None
            reference = self.file(pid, body.reference_file) if body.reference_file else (
                await self.reference(body.voice) if body.voice else None)
            required = bool(body.voice or body.reference_file)
            source = {'audio': body.audio}
        report = await asyncio.to_thread(gates.voice_gate, ff, audio, srt=srt, reference=reference, sections=sections,
                                         pitch_required=required)
        return self.save(pid, report, source)

    async def video(self, pid: str, body: VideoCheck) -> dict:
        ff = self.ffmpeg()
        report = await asyncio.to_thread(gates.video_gate, ff, self.file(pid, body.video), self.file(pid, body.voiceover))
        return self.save(pid, report, {'video': body.video, 'voiceover': body.voiceover})

    async def package(self, pid: str, body: PackageCheck) -> dict:
        meta = body.meta
        if meta is None:
            if not body.meta_file:
                raise ValueError('Give the metadata or a metadata JSON file.')
            try:
                meta = json.loads(self.file(pid, body.meta_file).read_text(encoding='utf-8-sig'))
            except ValueError as exc:
                raise ValueError(f'{body.meta_file} is not valid JSON: {exc}') from None
        seconds = await asyncio.to_thread(media.duration, self.ffmpeg(), self.file(pid, body.video)) if body.video else None
        thumb = self.file(pid, body.thumbnail) if body.thumbnail else None
        report = gates.package_gate(meta, video_seconds=seconds, thumbnail=thumb)
        return self.save(pid, report, {'video': body.video, 'thumbnail': body.thumbnail})

    def folder(self, pid: str) -> Path:
        d = self.store.files_dir(pid) / 'gates'
        d.mkdir(exist_ok=True)
        return d

    def save(self, pid: str, report: dict, source: dict) -> dict:
        report['source'] = source
        stamp, n = time.strftime('%Y%m%d-%H%M%S'), 0
        while (path := self.folder(pid) / f"{report['stage']}-{stamp}{f'-{n}' if n else ''}.json").exists():
            n += 1
        path.write_text(json.dumps(report, indent=1), encoding='utf-8')
        report['report'] = 'gates/' + path.name
        return report

    def list(self, pid: str) -> list[dict]:
        out = []
        for p in sorted(self.folder(pid).glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
            try:
                r = json.loads(p.read_text(encoding='utf-8'))
                out.append({'report': 'gates/' + p.name, 'stage': r['stage'], 'passed': r['passed'],
                            'summary': r['summary'], 'measured_at': r['measured_at']})
            except (ValueError, KeyError):
                continue
        return out


def mount(app, gate: Gates, project_check):
    async def guarded(pid, start):
        project_check(pid)
        try:
            return await start()
        except (ValueError, media.MediaError) as exc:
            raise HTTPException(400, str(exc))

    @app.post('/api/projects/{pid}/gates/voice')
    async def voice(pid: str, body: VoiceCheck):
        return await guarded(pid, lambda: gate.voice(pid, body))

    @app.post('/api/projects/{pid}/gates/video')
    async def video(pid: str, body: VideoCheck):
        return await guarded(pid, lambda: gate.video(pid, body))

    @app.post('/api/projects/{pid}/gates/package')
    async def package(pid: str, body: PackageCheck):
        return await guarded(pid, lambda: gate.package(pid, body))

    @app.get('/api/projects/{pid}/gates')
    def listing(pid: str):
        project_check(pid)
        return {'reports': gate.list(pid)}
