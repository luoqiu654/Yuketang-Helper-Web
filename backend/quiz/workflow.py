"""Explicit-confirmation workflow. A real remote verifier must prove OPEN.

No background process invokes submit.  Uncertain outcomes are terminal to
prevent a retry from duplicating a submission accepted by the server.
"""
import json
import time
from quiz import store
from quiz.validation import validate_answer

class WorkflowError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)

class UnavailableAdapter:
    def check(self, quiz):
        # The known presentation endpoint shows result but does not prove OPEN.
        return 'UNAVAILABLE'
    def submit(self, quiz, answer):
        raise RuntimeError('Remote submission is disabled')


def _finish(account, quiz_id, status, response_status, message):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM quizzes WHERE account_id=? AND id=?', (account, quiz_id)).fetchone()
        if not row or row['status'] != 'SUBMITTING':
            raise WorkflowError(409, 'Quiz changed')
        data = json.loads(row['data'])
        data['attempt_status'] = response_status
        data['attempt_message'] = message
        if status == 'SUBMITTED':
            data['submitted_at'] = time.time()
        db.execute('UPDATE quizzes SET data=?, status=?, revision=revision+1 WHERE account_id=? AND id=?',
                   (json.dumps(data, ensure_ascii=False), status, account, quiz_id))
        return store._public(db.execute('SELECT * FROM quizzes WHERE account_id=? AND id=?', (account, quiz_id)).fetchone())


def confirm(account, quiz_id, answer, revision, idempotency_key, adapter=None):
    adapter = adapter or UnavailableAdapter()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM quizzes WHERE account_id=? AND id=?', (account, quiz_id)).fetchone()
        if not row:
            raise WorkflowError(404, 'Quiz not found')
        data = json.loads(row['data'])
        if data.get('attempt_key') == idempotency_key:
            if data.get('final_answer') != answer:
                raise WorkflowError(409, 'Idempotency key used with a different answer')
            if row['status'] == 'SUBMITTING':
                raise WorkflowError(409, 'Confirmation is in progress')
            if data.get('attempt_status') == 200:
                return store._public(row)
            raise WorkflowError(data.get('attempt_status', 409), data.get('attempt_message', 'Confirmation already attempted'))
        if row['revision'] != revision or row['status'] not in store.PENDING:
            raise WorkflowError(409, 'Quiz changed or was already handled')
        problem = {'problemType': data['problem_type'], 'options': [{'key': x} for x in data['options']], 'pollingCount': data.get('max_options', 1)}
        try:
            answer = validate_answer(problem, answer)
        except ValueError:
            raise WorkflowError(422, 'Invalid answer') from None
        if data.get('deadline_at') and data['deadline_at'] <= time.time():
            db.execute("UPDATE quizzes SET status='EXPIRED', revision=revision+1 WHERE id=? AND account_id=?", (quiz_id, account))
            # Exiting the transaction normally is necessary to persist expiry.
            expired = True
        else:
            expired = False
            data.update(attempt_key=idempotency_key, confirmed_at=time.time(), final_answer=answer)
            data.pop('attempt_status', None)
            data.pop('attempt_message', None)
            db.execute("UPDATE quizzes SET status='SUBMITTING', data=?, revision=revision+1 WHERE id=? AND account_id=?", (json.dumps(data, ensure_ascii=False), quiz_id, account))
            item = store._public(db.execute('SELECT * FROM quizzes WHERE id=? AND account_id=?', (quiz_id, account)).fetchone())
    if expired:
        raise WorkflowError(410, 'Quiz expired')
    try:
        remote_state = adapter.check(item)
    except Exception:
        remote_state = 'UNAVAILABLE'
    if remote_state != 'OPEN':
        status, http, msg = {'ALREADY_ANSWERED': ('ALREADY_ANSWERED', 409, 'Already answered'),
                             'EXPIRED': ('EXPIRED', 410, 'Quiz expired')}.get(remote_state, ('NEEDS_MANUAL', 503, 'Question open status cannot be verified'))
        _finish(account, quiz_id, status, http, msg)
        raise WorkflowError(http, msg)
    if item.get('deadline_at') and item['deadline_at'] <= time.time():
        _finish(account, quiz_id, 'EXPIRED', 410, 'Quiz expired')
        raise WorkflowError(410, 'Quiz expired')
    try:
        accepted = adapter.submit(item, answer)
    except Exception:
        accepted = None
    if accepted is not True:
        # Even a network exception may mean the remote server accepted the answer.
        _finish(account, quiz_id, 'FAILED', 503, 'Submission outcome unknown; check in Yuketang')
        raise WorkflowError(503, 'Submission outcome unknown; check in Yuketang')
    return _finish(account, quiz_id, 'SUBMITTED', 200, 'Submitted')
