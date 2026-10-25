"""Protected Voiceovers routes sharing the agent-facing VoiceStudio service."""
import httpx
from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .storage import NotFound
from .voice_studio import MAX_REFERENCE, StudioError


class ConnectionIn(BaseModel):
    api_key: str | None = None
    model: str | None = None
    remove: bool = False


class AnalyzeIn(BaseModel):
    reference_id: str
    expected_speakers: int = Field(default=2, ge=1, le=2)
    language: str = Field(default='', max_length=12)
    approve: bool = False


class SpeakerIn(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    label: str = Field(default='', max_length=40)
    provider: str
    voice_id: str = Field(default='', max_length=180)
    channel_voice_id: str = ''
    use_clone_settings: bool = True
    reference_speaker: str = ''
    wpm: float | None = Field(default=None, ge=60, le=350)
    gap_ms: int | None = Field(default=None, ge=0, le=5000)
    pause_ms: int | None = Field(default=None, ge=0, le=2000)
    effect: str | None = None
    model: str = ''
    speed: float = Field(default=1, ge=.5, le=2)
    use_target_wpm: bool = True
    stability: float = Field(default=.5, ge=0, le=1)
    similarity: float = Field(default=.75, ge=0, le=1)
    style: float = Field(default=0, ge=0, le=1)
    pitch: int = Field(default=0, ge=-12, le=12)
    volume: float = Field(default=1, ge=.1, le=3)


class GenerateIn(BaseModel):
    name: str = Field(default='Voiceover', max_length=80)
    text: str = Field(default='', max_length=100000)
    file: str = ''
    format: str = 'shorts'
    reference_id: str = ''
    speakers: list[SpeakerIn] = Field(min_length=1, max_length=2)
    preview: bool = False
    approve: bool = False
    episode_number: str = Field(default='', max_length=8)
    consistent_pauses: bool = True
    pause_ms: int = Field(default=180, ge=0, le=2000)
    turn_gap_ms: int = Field(default=250, ge=0, le=5000)
    silence_threshold_db: float = Field(default=-42, ge=-65, le=-20)
    export_stems: bool = True


class ProfileIn(BaseModel):
    speakers: list[dict] = Field(max_length=10)


class CloneIn(BaseModel):
    consent: bool = False


class PrepareVoiceIn(BaseModel):
    provider: str
    approve: bool = False


class CloneCheckIn(BaseModel):
    provider: str = 'chatterbox'
    test_text: str = Field(default='', max_length=600)
    attempts: int = Field(default=5, ge=1, le=10)
    auto_retry: bool = True
    target_similarity: float | None = Field(default=None, ge=0, le=1)
    approve: bool = False


class RepairIn(BaseModel):
    sections: list[int] = Field(min_length=1, max_length=20)
    reason: str = Field(default='', max_length=1000)
    approve: bool = False


def mount(app, studio, project, web_dir):
    def asset(name, mime):
        def serve():
            return FileResponse(web_dir / name, media_type=mime)
        return serve
    for url, filename, mime in [('/voiceovers', 'voiceovers.html', 'text/html'),
                                ('/voiceovers.js', 'voiceovers.js', 'text/javascript'),
                                ('/voiceovers.css', 'voiceovers.css', 'text/css')]:
        app.add_api_route(url, asset(filename, mime), methods=['GET'], include_in_schema=False)

    def fail(exc):
        raise HTTPException(404 if isinstance(exc, NotFound) else 400,
                            'Project file not found.' if isinstance(exc, NotFound) else str(exc))

    @app.get('/api/voice-studio/providers')
    def providers():
        return studio.provider_status()

    @app.put('/api/voice-studio/providers/{provider}')
    def connection(provider: str, body: ConnectionIn):
        try:
            return studio.configure(provider, body.api_key, body.model, body.remove)
        except StudioError as exc:
            fail(exc)

    @app.get('/api/voice-studio/voices/{provider}')
    async def voices(provider: str):
        try:
            return await studio.voices(provider)
        except httpx.HTTPError:
            raise HTTPException(502, 'The voice provider could not be reached. Check your connection and try again.')
        except ValueError as exc:
            fail(exc)

    @app.get('/api/projects/{pid}/voice-studio')
    def state(pid: str):
        project(pid)
        return {'references': studio.references(pid), 'jobs': studio.jobs(pid), 'channel_voices': studio.channel_voices(pid)}

    @app.post('/api/projects/{pid}/voice-studio/voices')
    async def add_channel_voice(pid: str, name: str = Form(...), consent: bool = Form(False), file: UploadFile = File(...)):
        project(pid)
        try:
            return await studio.add_channel_voice(pid, name, file.filename or 'voice.wav', await file.read(MAX_REFERENCE + 1), consent)
        except (ValueError, NotFound) as exc:
            fail(exc)

    @app.post('/api/projects/{pid}/voice-studio/voices/{vid}/processed')
    async def processed(pid: str, vid: str, file: UploadFile = File(...)):
        project(pid)
        try:
            return await studio.add_processed_sample(pid, vid, file.filename or 'processed.wav', await file.read(MAX_REFERENCE + 1))
        except (ValueError, NotFound) as exc:
            fail(exc)

    @app.post('/api/projects/{pid}/voice-studio/voices/{vid}/prepare')
    async def prepare(pid: str, vid: str, body: PrepareVoiceIn):
        project(pid)
        if not body.approve:
            raise HTTPException(409, 'Approve preparing this voice. Cloud cloning uploads the clean reference and may use credits/voice slots.')
        try:
            return studio.prepare_channel_voice(pid, vid, body.provider, approved=True)
        except (ValueError, NotFound) as exc:
            fail(exc)

    @app.post('/api/projects/{pid}/voice-studio/voices/{vid}/clone-check')
    async def clone_check(pid: str, vid: str, body: CloneCheckIn):
        project(pid)
        if not body.approve:
            raise HTTPException(409, 'Approve cloning and comparison speech. Cloud providers can use credits and voice slots.')
        try:
            return studio.start_clone_check(pid, vid, body.provider, body.test_text, body.attempts, approved=True,
                                           auto_retry=body.auto_retry, target_similarity=body.target_similarity)
        except (ValueError, NotFound) as exc:
            fail(exc)

    @app.post('/api/projects/{pid}/voice-studio/references')
    async def upload(pid: str, file: UploadFile = File(...), kind: str = Form('channel'), consent: bool = Form(False)):
        project(pid)
        try:
            return await studio.add_reference(pid, file.filename or 'reference.wav', await file.read(MAX_REFERENCE + 1), kind, consent)
        except (ValueError, NotFound) as exc:
            fail(exc)

    @app.post('/api/projects/{pid}/voice-studio/analyze')
    async def analyze(pid: str, body: AnalyzeIn):
        project(pid)
        if not body.approve:
            raise HTTPException(409, 'Approve analysis: this audio is sent to ElevenLabs Scribe and uses API credits.')
        try:
            return studio.start_analysis(pid, body.reference_id, body.expected_speakers, body.language, approved=True)
        except (ValueError, NotFound) as exc:
            fail(exc)

    @app.patch('/api/projects/{pid}/voice-studio/references/{rid}')
    def profile(pid: str, rid: str, body: ProfileIn):
        project(pid)
        try:
            return studio.update_profile(pid, rid, body.speakers)
        except (ValueError, KeyError, TypeError) as exc:
            fail(exc)

    @app.post('/api/projects/{pid}/voice-studio/references/{rid}/clone/{sid}')
    async def clone(pid: str, rid: str, sid: str, body: CloneIn):
        project(pid)
        try:
            return await studio.clone_reference(pid, rid, sid, body.consent)
        except (ValueError, NotFound) as exc:
            fail(exc)

    @app.post('/api/projects/{pid}/voice-studio/plan')
    def plan(pid: str, body: GenerateIn):
        project(pid)
        try:
            return studio.plan(pid, body.model_dump())
        except (ValueError, NotFound) as exc:
            fail(exc)

    @app.post('/api/projects/{pid}/voice-studio/generate')
    async def generate(pid: str, body: GenerateIn):
        project(pid)
        if not body.approve:
            raise HTTPException(409, 'Approve generation: cloud providers consume credits; Chatterbox uses the voice workstation.')
        try:
            return studio.start_generation(pid, body.model_dump(), approved=True)
        except (ValueError, NotFound) as exc:
            fail(exc)

    @app.get('/api/projects/{pid}/voice-studio/jobs/{jid}')
    def status(pid: str, jid: str):
        project(pid)
        try:
            return studio.job(pid, jid)
        except StudioError as exc:
            fail(exc)

    @app.post('/api/projects/{pid}/voice-studio/jobs/{jid}/repair')
    async def repair(pid: str, jid: str, body: RepairIn):
        project(pid)
        if not body.approve:
            raise HTTPException(409, 'Approve repairing these passages. Selected sections are regenerated and can use provider credits.')
        try:
            return studio.start_repair(pid, jid, body.sections, body.reason, approved=True)
        except (ValueError, NotFound) as exc:
            fail(exc)

    @app.post('/api/projects/{pid}/voice-studio/jobs/{jid}/cancel')
    async def cancel(pid: str, jid: str):
        project(pid)
        try:
            return studio.cancel(pid, jid)
        except StudioError as exc:
            fail(exc)
