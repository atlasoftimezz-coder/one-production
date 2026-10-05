"""Protected project-scoped library routes; only deliberate uploads become sources."""
import asyncio
import secrets
from pathlib import Path

from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .knowledge import MAX_BYTES, SUPPORTED
from .storage import safe_filename


class LibraryContext(BaseModel):
    channel: str = Field(default='', max_length=100)
    version: str = Field(default='', max_length=40)


def mount(app, library, project_check, web_dir):
    @app.get('/knowledge.js')
    def script():
        return FileResponse(web_dir / 'knowledge.js', media_type='text/javascript')

    @app.get('/api/projects/{pid}/knowledge')
    def listing(pid: str):
        project_check(pid)
        return {'documents': library.list(pid), 'context': library.context(pid)}

    @app.patch('/api/projects/{pid}/knowledge/context')
    def context(pid: str, body: LibraryContext):
        project_check(pid)
        library.set_context(pid, body.channel, body.version)
        return library.context(pid)

    @app.post('/api/projects/{pid}/knowledge')
    async def upload(pid: str, file: UploadFile = File(...), scope: str = Form('project'),
                     application: str = Form(''), version: str = Form('')):
        project_check(pid)
        name = safe_filename(file.filename or 'document')
        if Path(name).suffix.lower() not in SUPPORTED:
            raise HTTPException(400, 'Choose a PDF, DOCX, Markdown or TXT document.')
        incoming = library.root / 'incoming'
        incoming.mkdir(exist_ok=True)
        path = incoming / secrets.token_hex(16)
        try:
            size = 0
            with path.open('wb') as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(413, 'Document exceeds the 500 MB limit.')
                    out.write(chunk)
            return await asyncio.to_thread(library.add, path, name, pid, scope=scope, application=application, version=version)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        finally:
            path.unlink(missing_ok=True)
            await file.close()

    @app.get('/api/projects/{pid}/knowledge/search')
    def search(pid: str, q: str = '', application: str = '', version: str = ''):
        project_check(pid)
        return library.search(pid, q[:1000], application=application, version=version)

    @app.get('/api/projects/{pid}/knowledge/{doc_id}/source')
    def source(pid: str, doc_id: str):
        project_check(pid)
        try:
            doc = library.get(doc_id, pid)
            return FileResponse(library.source(doc_id, pid), filename=doc['name'])
        except ValueError as exc:
            raise HTTPException(404, str(exc))

    @app.post('/api/projects/{pid}/knowledge/{doc_id}/{action}')
    def manage(pid: str, doc_id: str, action: str):
        project_check(pid)
        if action not in ('cancel', 'retry', 'remove'):
            raise HTTPException(404, 'Unknown library action.')
        try:
            getattr(library, action)(doc_id, pid)
            return {'ok': True}
        except ValueError as exc:
            raise HTTPException(400, str(exc))
