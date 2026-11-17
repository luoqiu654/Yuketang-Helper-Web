"""Offline checks for the temporary V0.2 submission safety gate."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

with tempfile.TemporaryDirectory() as store:
    os.environ['YUKETANG_STORE_DIR'] = store
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
    from lesson import Lesson
    from quiz import store as quiz_store

class SafetyGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_patch = patch.object(quiz_store, 'DB', Path(self.tmp.name) / 'quiz.sqlite3')
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.events = []
        self.lesson = Lesson('test', {'lessonid': 1, 'lessonname': 'test', 'classroomid': 2},
                             'dummy', 'pro.yuketang.cn', {'type1': 'ai'},
                             lambda kind, data: self.events.append((kind, data)))

    def test_legacy_modes_do_not_call_ai_or_submit(self):
        for index, mode in enumerate(('ai', 'random', 'off')):
            self.lesson.course_config['type1'] = mode
            problem = {'problemId': index, 'problemType': 1, 'result': None}
            with patch('lesson.http_request', side_effect=AssertionError('network called')):
                self.lesson._answer_if_needed(problem, 10, 'test')
                self.lesson._answer_if_needed(problem, 10, 'duplicate')
                self.assertFalse(self.lesson._submit_answer(index, 1, ['A'], mode))
        self.assertEqual(len(self.events), 3)

if __name__ == '__main__':
    unittest.main()
