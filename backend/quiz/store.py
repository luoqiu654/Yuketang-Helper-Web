"""Durable, account-scoped quiz review records."""
import json
from contextlib import contextmanager
import sqlite3
import time
import uuid
from config import STORE_DIR

DB = STORE_DIR / 'quiz_state.sqlite3'
PENDING = ('RECEIVED', 'SUGGESTING', 'NEEDS_MANUAL', 'AWAITING_CONFIRMATION')

@contextmanager
def connect():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS quizzes (
      id TEXT PRIMARY KEY, account_id TEXT NOT NULL, lesson_id TEXT NOT NULL,
      problem_id TEXT NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL,
      revision INTEGER NOT NULL DEFAULT 0, received_at REAL NOT NULL,
      UNIQUE(account_id, lesson_id, problem_id))''')
    try:
        with db:
            yield db
    finally:
        db.close()

def _public(row):
    return dict(id=row['id'], lesson_id=row['lesson_id'], problem_id=row['problem_id'],
                status=row['status'], revision=row['revision'], received_at=row['received_at'],
                **json.loads(row['data']))

def create(account, lesson, problem):
    limit, sent = problem.get('limit'), problem.get('sendTime')
    deadline = float(sent) / 1000 + float(limit) if limit and sent and float(limit) > 0 else None
    data = {'lesson': lesson.lessonname, 'classroom_id': str(lesson.classroomid),
            'problem_type': problem['problemType'], 'cover': problem.get('_cover', ''),
            'options': [str(x.get('key', '')) for x in problem.get('options', [])],
            'max_options': int(problem.get('pollingCount') or 1),
            'deadline_at': deadline, 'suggested_answer': None, 'model_id': None,
            'provider_id': None, 'latency_ms': None, 'error_code': None}
    with connect() as db:
        cur = db.execute('INSERT OR IGNORE INTO quizzes VALUES (?, ?, ?, ?, ?, ?, 0, ?)',
                         (str(uuid.uuid4()), account, str(lesson.lessonid), str(problem['problemId']),
                          json.dumps(data, ensure_ascii=False), 'NEEDS_MANUAL', time.time()))
        return cur.rowcount == 1

def get(account, quiz_id):
    with connect() as db:
        row = db.execute('SELECT * FROM quizzes WHERE account_id=? AND id=?', (account, quiz_id)).fetchone()
        return _public(row) if row else None

def get_by_problem(account, lesson_id, problem_id):
    with connect() as db:
        row = db.execute('SELECT * FROM quizzes WHERE account_id=? AND lesson_id=? AND problem_id=?', (account, str(lesson_id), str(problem_id))).fetchone()
        return _public(row) if row else None

def transition(account, quiz_id, expected, status, updates=None):
    """Atomically mutate an active record; late AI results cannot revive it."""
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM quizzes WHERE account_id=? AND id=?', (account, quiz_id)).fetchone()
        if not row or row['status'] not in expected:
            return False
        data = json.loads(row['data'])
        if status in ('SUGGESTING', 'AWAITING_CONFIRMATION') and data.get('deadline_at') and data['deadline_at'] <= time.time():
            db.execute("UPDATE quizzes SET status='EXPIRED',revision=revision+1 WHERE id=? AND account_id=?", (quiz_id, account))
            return False
        data.update(updates or {})
        db.execute('UPDATE quizzes SET status=?,data=?,revision=revision+1 WHERE id=? AND account_id=?',
                   (status, json.dumps(data, ensure_ascii=False), quiz_id, account))
        return True

def recover_interrupted():
    """An AI worker cannot survive a process restart."""
    with connect() as db:
        db.execute("UPDATE quizzes SET status='NEEDS_MANUAL', revision=revision+1 WHERE status='SUGGESTING'")
        # A process may have stopped after the remote server accepted a POST.
        # Never retry it automatically when its outcome is unknown.
        db.execute("UPDATE quizzes SET status='FAILED', revision=revision+1 WHERE status='SUBMITTING'")

def mark_answered(account, lesson_id, problem_id):
    row = get_by_problem(account, lesson_id, problem_id)
    return bool(row and transition(account, row['id'], PENDING, 'ALREADY_ANSWERED'))

def list_pending(account):
    with connect() as db:
        rows = db.execute("SELECT * FROM quizzes WHERE account_id=? AND status IN ('RECEIVED','SUGGESTING','NEEDS_MANUAL','AWAITING_CONFIRMATION') ORDER BY received_at DESC", (account,)).fetchall()
    result = []
    for row in rows:
        item = _public(row)
        if item.get('deadline_at') and item['deadline_at'] <= time.time():
            transition(account, item['id'], PENDING, 'EXPIRED')
        else:
            result.append(item)
    return result

def dismiss(account, quiz_id, revision):
    with connect() as db:
        cur = db.execute("UPDATE quizzes SET status='DISMISSED', revision=revision+1 WHERE id=? AND account_id=? AND revision=? AND status IN ('RECEIVED','SUGGESTING','NEEDS_MANUAL','AWAITING_CONFIRMATION')", (quiz_id, account, revision))
        return cur.rowcount == 1
