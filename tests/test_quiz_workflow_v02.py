import os, sys, tempfile, unittest, time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from quiz import store
from quiz.validation import validate_answer
from lesson import Lesson

class QuizWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.db = patch.object(store, 'DB', Path(self.temp.name) / 'quiz.sqlite3'); self.db.start(); self.addCleanup(self.db.stop)
        self.events = []
        self.lesson = Lesson('a', {'lessonid': 1, 'lessonname': 'course', 'classroomid': 2}, '', 'pro.yuketang.cn', {'type1': 'ai_suggest_confirm'}, lambda typ, data: self.events.append((typ, data)))
        self.problem = {'problemId': 42, 'problemType': 1, 'options': [{'key': 'A'}, {'key': 'B'}], 'result': None}

    def test_validation(self):
        self.assertEqual(validate_answer(self.problem, ['A']), ['A'])
        for invalid in ([], ['A','B'], ['A','A'], ['X'], 'A'):
            with self.assertRaises(ValueError): validate_answer(self.problem, invalid)
        self.assertEqual(validate_answer({'problemType': 5}, ' text '), 'text')
        with self.assertRaises(ValueError): validate_answer({'problemType': 5}, '  ')

    def test_suggestion_never_submits_and_dedup(self):
        import ai_gateway
        with patch.object(ai_gateway, 'suggest', return_value={'answer':['A'], 'model_id':'m','provider_id':'p','latency_ms':1}), patch('lesson.http_request', side_effect=AssertionError('submission must not occur')):
            self.lesson._answer_if_needed(self.problem, 20, 'live')
            for _ in range(100):
                q = store.get_by_problem('a', 1, 42)
                if q and q['status'] != 'SUGGESTING': break
                time.sleep(.01)
            self.assertEqual(q['status'], 'AWAITING_CONFIRMATION')
            self.assertEqual(q['suggested_answer'], ['A'])
            self.lesson._answer_if_needed(self.problem, 20, 'reconcile')
            self.assertEqual(len(store.list_pending('a')), 1)
            self.assertEqual(sum(x[0] == 'problem_received' for x in self.events), 1)

    def test_answer_elsewhere_and_late_suggestion(self):
        store.create('a', self.lesson, self.problem)
        q = store.get_by_problem('a', 1, 42)
        self.assertTrue(store.transition('a', q['id'], ('NEEDS_MANUAL',), 'SUGGESTING'))
        self.assertTrue(store.mark_answered('a', 1, 42))
        self.assertFalse(store.transition('a', q['id'], ('SUGGESTING',), 'AWAITING_CONFIRMATION'))
        self.assertEqual(store.get('a', q['id'])['status'], 'ALREADY_ANSWERED')

    def test_expiry_and_interrupted_recovery(self):
        self.problem.update(limit=1, sendTime=(time.time() - 5) * 1000)
        store.create('a', self.lesson, self.problem)
        q = store.get_by_problem('a', 1, 42)
        self.assertFalse(store.transition('a', q['id'], ('NEEDS_MANUAL',), 'SUGGESTING'))
        self.assertEqual(store.get('a', q['id'])['status'], 'EXPIRED')
        self.problem.update(problemId=43, limit=0)
        store.create('a', self.lesson, self.problem)
        q = store.get_by_problem('a', 1, 43)
        self.assertTrue(store.transition('a', q['id'], ('NEEDS_MANUAL',), 'SUGGESTING'))
        store.recover_interrupted()
        self.assertEqual(store.get('a', q['id'])['status'], 'NEEDS_MANUAL')

    def test_confirmation_fails_closed(self):
        from routers.quizzes import confirm, Confirmation
        from fastapi import HTTPException
        store.create('a', self.lesson, self.problem)
        q = store.get_by_problem('a', 1, 42)
        body = Confirmation(answer=['A'], expected_revision=q['revision'], idempotency_key='test')
        with patch('lesson.http_request', side_effect=AssertionError('network must not be called')):
            with self.assertRaises(HTTPException) as caught:
                confirm(q['id'], body, account_id='a')
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(store.get('a', q['id'])['status'], 'NEEDS_MANUAL')

if __name__ == '__main__': unittest.main()
