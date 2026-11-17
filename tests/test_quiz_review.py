import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from quiz import store
from lesson import Lesson

class QuizStoreTests(unittest.TestCase):
    def test_dedup_and_account_scoped_dismiss(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(store, 'DB', Path(folder) / 'quiz.sqlite3'):
            events = []
            lesson = Lesson('a', {'lessonid': 1, 'lessonname': 'class', 'classroomid': 2}, '', 'pro.yuketang.cn', {'type1': 'ai'}, lambda kind, data: events.append(kind))
            problem = {'problemId': 7, 'problemType': 1, 'result': None, 'options': [{'key': 'A'}]}
            with patch('lesson.http_request', side_effect=AssertionError('unexpected network')):
                lesson._answer_if_needed(problem, 10, 'live')
                lesson._answer_if_needed(problem, 10, 'recovery')
                self.assertFalse(lesson._submit_answer(7, 1, ['A'], 'ai'))
            self.assertEqual(events, ['problem_received'])
            q = store.list_pending('a')[0]
            self.assertEqual(store.list_pending('b'), [])
            self.assertFalse(store.dismiss('b', q['id'], q['revision']))
            self.assertTrue(store.dismiss('a', q['id'], q['revision']))
            self.assertFalse(store.dismiss('a', q['id'], q['revision']))
            self.assertEqual(store.list_pending('a'), [])

if __name__ == '__main__': unittest.main()
