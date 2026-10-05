"""Protected render routes. A render is CPU-heavy, so it needs the heavy-job approval (R12)."""
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .editor import EditError
from .media import MediaError


class RenderIn(BaseModel):
    edit: str
    approve: str | None = None          # None (ask) | "once" | "always"


class BuildIn(BaseModel):
    shots: str
    edit: str = ''


def mount(app, editor, approvals, project_check, animation=None):
    def guarded(pid, fn):
        project_check(pid)
        try:
            return fn()
        except (EditError, MediaError) as exc:
            raise HTTPException(400, str(exc))

    @app.post('/api/projects/{pid}/edit/check')
    def check(pid: str, body: RenderIn):
        """Validate an edit list without rendering (no approval needed)."""
        def run():
            _, p, voiceover = editor.prepare(pid, body.edit)
            return {'ok': True, 'visuals': len(p['visuals']), 'seconds': round(p['seconds'], 3), 'fps': p['fps'],
                    'size': p['size'], 'fitted_by': p['fitted_by'], 'voiceover': voiceover.name}
        return guarded(pid, run)

    @app.post('/api/projects/{pid}/animation/build')
    def build(pid: str, body: BuildIn):
        """Drawings -> frames -> timed edit list. Light CPU (the browser's screenshot), no approval."""
        return guarded(pid, lambda: animation.build(pid, body.shots, body.edit))

    @app.post('/api/projects/{pid}/edit/render')
    def render(pid: str, body: RenderIn):
        project_check(pid)
        need = approvals.check('video_render', body.approve)
        if need:
            return JSONResponse({'needs_approval': need}, status_code=409)
        return guarded(pid, lambda: editor.start(pid, body.edit))

    @app.get('/api/projects/{pid}/edit/jobs')
    def jobs(pid: str):
        project_check(pid)
        return {'jobs': editor.list(pid)}

    @app.get('/api/projects/{pid}/edit/jobs/{job_id}')
    def job(pid: str, job_id: str):
        return guarded(pid, lambda: editor.get(pid, job_id))

    @app.post('/api/projects/{pid}/edit/jobs/{job_id}/cancel')
    def cancel(pid: str, job_id: str):
        return guarded(pid, lambda: editor.cancel(pid, job_id))
