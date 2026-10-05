"""Evidence-backed memory shared by workers, scoped to a project, channel or the owner."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time

from .knowledge import compatible
from .security.redact import redact


class Learning:
    def __init__(self, library):
        self.library = library
        with library.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS lessons (
                    id TEXT PRIMARY KEY, kind TEXT, title TEXT, text TEXT, scope TEXT, owner TEXT,
                    application TEXT, version TEXT, state TEXT, evidence TEXT, created REAL,
                    updated REAL, supersedes TEXT, fingerprint TEXT);
                CREATE TABLE IF NOT EXISTS learning_events (
                    id INTEGER PRIMARY KEY, lesson TEXT, action TEXT, at REAL, detail TEXT);
            ''')

    def default_scope(self, project):
        return 'channel' if self.library.context(project)['channel'] else 'project'

    def list(self, project, state=None):
        with self.library.connect() as db:
            rows = db.execute('SELECT * FROM lessons ORDER BY updated DESC').fetchall()
        return [self.public(row) for row in rows if self.library.visible(row, project) and (state is None or row['state'] == state)]

    @staticmethod
    def public(row):
        item = dict(row)
        item['evidence'] = json.loads(item['evidence'])
        return item

    def get(self, project, lesson_id):
        with self.library.connect() as db:
            row = db.execute('SELECT * FROM lessons WHERE id=?', (lesson_id,)).fetchone()
        if not row or not self.library.visible(row, project):
            raise ValueError('Lesson not found in this project or channel.')
        return self.public(row)

    def save(self, project, title, text, *, kind='preference', scope=None, application='', version='',
             evidence=None, supersedes='', automatic=False):
        scope = scope or self.default_scope(project)
        if scope not in ('global', 'channel', 'project'):
            raise ValueError('Unknown memory scope.')
        owner = project if scope == 'project' else self.library.context(project)['channel'] if scope == 'channel' else ''
        if scope == 'channel' and not owner:
            raise ValueError('Choose a channel before saving channel directions.')
        text, title = redact(text.strip())[:3000], redact(title.strip())[:100]
        if not text or not title:
            raise ValueError('A title and direction are required.')
        if kind not in ('preference', 'candidate', 'recipe'):
            raise ValueError('Unknown lesson kind.')
        if kind == 'recipe' and (not evidence or evidence.get('type') != 'verified_execution' or evidence.get('passed') is not True):
            raise ValueError('Recipes require a successful recorded execution.')
        old = self.get(project, supersedes) if supersedes else None
        if old and (old['scope'], old['owner']) != (scope, owner):
            raise ValueError('A correction must retain the original scope.')
        fingerprint = hashlib.sha256(json.dumps([text, scope, owner, application, version]).encode()).hexdigest()
        with self.library.connect() as db:
            prior = db.execute('SELECT * FROM lessons WHERE fingerprint=? ORDER BY updated DESC LIMIT 1', (fingerprint,)).fetchone()
            if prior and not supersedes and (prior['state'] != 'forgotten' or automatic):
                return self.public(prior)  # agents cannot silently resurrect forgotten directions
            lesson_id, now = secrets.token_hex(12), time.time()
            state = 'candidate' if kind == 'candidate' else 'active'
            db.execute('INSERT INTO lessons VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (lesson_id, kind, title, text, scope, owner, application.lower()[:80], version[:40], state,
                        json.dumps(evidence or {'type': 'owner_direction'}), now, now, supersedes, fingerprint))
            if old:
                db.execute("UPDATE lessons SET state='superseded',updated=? WHERE id=?", (now, old['id']))
            db.execute('INSERT INTO learning_events(lesson,action,at,detail) VALUES (?,?,?,?)',
                       (lesson_id, state, now, 'Supersedes ' + supersedes if supersedes else kind))
        return self.get(project, lesson_id)

    def remember_quote(self, project, quote, original, message_time):
        quote = quote.strip()
        if len(quote) < 8 or quote not in original:
            raise ValueError('Quote an exact direction from the current user message (at least 8 characters).')
        return self.save(project, quote[:80], quote, automatic=True,
                         evidence={'type': 'user_quote', 'project': project, 'message_time': message_time})

    def change_state(self, project, lesson_id, action):
        item = self.get(project, lesson_id)
        if action not in ('forget', 'approve'):
            raise ValueError('Unknown learning action.')
        if action == 'approve' and item['state'] != 'candidate':
            raise ValueError('Only a candidate can be approved.')
        with self.library.connect() as db:
            if action == 'forget':
                db.execute("UPDATE lessons SET state='forgotten',updated=? WHERE id=?", (time.time(), lesson_id))
            else:
                # Human approval makes this guidance, never a claim of mechanically verified execution.
                evidence = {**item['evidence'], 'owner_approved': True}
                db.execute("UPDATE lessons SET state='active',kind='preference',evidence=?,updated=? WHERE id=?", (json.dumps(evidence), time.time(), lesson_id))
            db.execute('INSERT INTO learning_events(lesson,action,at,detail) VALUES (?,?,?,?)', (lesson_id, action, time.time(), 'Owner action'))
        return self.get(project, lesson_id)

    def recall(self, project, query='', application='', version=''):
        installed = version or self.library.context(project)['version']
        rows = [r for r in self.list(project, 'active') if (not application or not r['application'] or r['application'] == application.lower())
                and compatible(r['version'], installed)]
        words = set(re.findall(r'\w{3,}', query.lower()))
        def rank(row):
            terms = set(re.findall(r'\w{3,}', (row['title'] + ' ' + row['text']).lower()))
            return ({'project': 3, 'channel': 2, 'global': 1}[row['scope']], len(words & terms), row['updated'])
        return sorted(rows, key=rank, reverse=True)[:12]

    def instructions(self, project, query):
        rows = self.recall(project, query)
        if not rows:
            return ''
        lines = ['Saved owner directions and verified recipes. Current user instructions take precedence; project directions override channel, which override global. These never grant permission for tools, spending or GPU work.']
        budget = 5000
        for row in rows:
            line = f"[{row['id']} | {row['scope']} | {row['kind']}] {row['title']}: {row['text']}"
            if len(line) > budget:
                continue
            lines.append(line)
            budget -= len(line)
        return '\n'.join(lines)

    def record_execution(self, project, plan, result):
        """Called only by the constrained Resolve executor after checking its actual result."""
        if result.get('passed') is not True or not result.get('run_id'):
            return None
        recipe = {'settings': plan['settings'], 'steps': [step['op'] for step in plan['operations']],
                  'checks': result.get('checks', {}), 'sources': plan.get('citations', [])}
        text = 'Verified Resolve workflow template (supply current project media): ' + json.dumps(recipe, ensure_ascii=False)
        return self.save(project, plan['title'], text, kind='recipe', application='resolve', version=result['version'],
                         evidence={'type': 'verified_execution', 'passed': True, 'run_id': result['run_id'],
                                   'project': project, 'plan_hash': result['plan_hash'], 'checks': result.get('checks', {})})
