from fastapi import HTTPException
from pydantic import BaseModel, Field


class Direction(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=3000)
    scope: str | None = None
    application: str = Field(default='', max_length=80)
    version: str = Field(default='', max_length=40)
    supersedes: str = ''


def mount(app, learning, project_check):
    @app.get('/api/projects/{pid}/learning')
    def listing(pid: str):
        project_check(pid)
        return {'lessons': learning.list(pid), 'default_scope': learning.default_scope(pid)}

    @app.post('/api/projects/{pid}/learning')
    def save(pid: str, body: Direction):
        project_check(pid)
        try:
            return learning.save(pid, **body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.post('/api/projects/{pid}/learning/{lesson_id}/{action}')
    def change(pid: str, lesson_id: str, action: str):
        project_check(pid)
        try:
            return learning.change_state(pid, lesson_id, action)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
