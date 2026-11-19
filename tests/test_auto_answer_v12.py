"""Offline tests for opt-in automatic answers; no Yuketang network calls."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory()
os.environ['YUKETANG_STORE_DIR'] = _temp.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from lesson import Lesson
from quiz import store
from config import CourseSettingsModel


class AutoAnswerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(store, 'DB', Path(self.temp.name) / 'quiz.sqlite3')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.events = []
        self.lesson = Lesson('a', {'lessonid': 1, 'lessonname': 'course', 'classroomid': 2}, '',
                             'pro.yuketang.cn', {'type1': 'ai_auto', 'type5': 'ai_auto'},
                             lambda kind, data: self.events.append((kind, data)))
        self.problem = {'problemId': 42, 'problemType': 1, 'options': [{'key': 'A'}, {'key': 'B'}],
                        'result': None, '_pres': 9}
        store.create('a', self.lesson, self.problem)
        self.quiz = store.get_by_problem('a', 1, 42)
        store.transition('a', self.quiz['id'], ('NEEDS_MANUAL',), 'SUGGESTING')
        self.lesson._remember_unlocked(42)
        self.result = {'model_id': 'fake', 'provider_id': 'fake', 'latency_ms': 1}

    def run_auto(self, answer=None, deadline=None, refresh=True, post=None):
        answer = ['A'] if answer is None else answer
        def fetch(*args, **kwargs):
            if not refresh:
                raise TimeoutError()
            self.lesson._problems[self.problem['problemId']] = dict(self.problem)
            return {self.problem['problemId']: dict(self.problem)}
        reply = SimpleNamespace(status_code=200, json=lambda: {'code': 0})
        with patch.object(self.lesson, '_fetch_presentation', side_effect=fetch), \
             patch('lesson.http_request', side_effect=post or (lambda *a, **k: reply)) as request:
            self.lesson._auto_submit(self.quiz['id'], self.problem, answer, self.result, deadline)
        return request

    def status(self):
        return store.get('a', self.quiz['id'])['status']

    def test_explicit_mode_is_not_default(self):
        self.assertEqual(CourseSettingsModel().type1, 'notify_only')
        self.assertEqual(CourseSettingsModel(type1='ai_auto').type1, 'ai_auto')

    def test_valid_answer_submits_once_and_saves_result(self):
        request = self.run_auto()
        self.assertEqual(self.status(), 'SUBMITTED')
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.kwargs['attempts'], 1)
        self.assertFalse(request.call_args.kwargs['allow_redirects'])
        self.assertEqual(request.call_args.kwargs['json']['result'], ['A'])
        self.run_auto(post=lambda *a, **k: self.fail('duplicate POST'))

    def test_unlocked_required_and_expiry(self):
        self.lesson._unlocked_seen.clear()
        request = self.run_auto()
        self.assertEqual(request.call_count, 0)
        self.assertEqual(self.status(), 'NEEDS_MANUAL')
        self.assertTrue(any(d.get('message') == 'AUTO_OPEN_UNVERIFIED' for _, d in self.events))

    def test_expired_does_not_post(self):
        request = self.run_auto(deadline=time.time()-1)
        self.assertEqual(request.call_count, 0)
        self.assertEqual(self.status(), 'EXPIRED')

    def test_answered_elsewhere_never_posts(self):
        self.problem['result'] = ['B']
        request = self.run_auto()
        self.assertEqual(request.call_count, 0)
        self.assertEqual(self.status(), 'ALREADY_ANSWERED')

    def test_refresh_failure_does_not_use_cache(self):
        self.lesson._problems[42] = dict(self.problem)
        request = self.run_auto(refresh=False)
        self.assertEqual(request.call_count, 0)
        self.assertEqual(self.status(), 'NEEDS_MANUAL')

    def test_ambiguous_post_not_retried(self):
        def timeout(*a, **k): raise TimeoutError()
        request = self.run_auto(post=timeout)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.status(), 'FAILED')
        self.assertEqual(store.list_pending('a')[0]['status'], 'FAILED')
        self.assertTrue(any(d.get('message') == 'AUTO_SUBMISSION_UNKNOWN_CHECK_YUKETANG' for _, d in self.events))
        self.run_auto(post=lambda *a, **k: self.fail('ambiguous POST retried'))

    def test_auto_disabled_after_ai_does_not_post(self):
        self.lesson.course_config['type1'] = 'notify_only'
        request = self.run_auto()
        self.assertEqual(request.call_count, 0)

    def test_short_answer_wire_format(self):
        self.problem['problemType'] = 5
        request = self.run_auto(answer='Example')
        self.assertEqual(request.call_args.kwargs['json']['result'],
                         {'content': 'Example', 'pics': [{'pic': '', 'thumb': ''}]})

    def test_refreshed_presentation_missing_problem_does_not_use_cache(self):
        self.lesson._problems[42] = dict(self.problem)
        response = SimpleNamespace(status_code=200, json=lambda: {'code': 0, 'data': {'slides': []}})
        with patch('lesson.http_request', return_value=response) as request:
            self.lesson._auto_submit(self.quiz['id'], self.problem, ['A'], self.result, None)
        self.assertEqual([call.args[0] for call in request.call_args_list], ['GET'])
        self.assertEqual(self.status(), 'NEEDS_MANUAL')

    def test_stale_or_invalid_ai_answer_never_posts(self):
        request = self.run_auto(answer=['Z'])
        self.assertEqual(request.call_count, 0)
        self.assertEqual(self.status(), 'NEEDS_MANUAL')

    def test_one_second_frame_is_not_treated_as_untimed(self):
        self.lesson._problems[42] = dict(self.problem)
        with patch.object(self.lesson, '_answer_if_needed') as answer:
            self.lesson._dispatch({'op': 'unlockproblem', 'problem': {'prob': 42, 'limit': 1}})
        self.assertEqual(answer.call_args.args[1], 0)

    def test_frame_deadline_starts_before_slow_presentation_fetch(self):
        clock = {'now': time.time()}
        received = clock['now']
        def slow_load(_):
            clock['now'] += 8
            self.lesson._problems[42] = dict(self.problem)
        with patch('lesson.time.time', side_effect=lambda: clock['now']), \
             patch.object(self.lesson, '_load_presentations', side_effect=slow_load), \
             patch.object(self.lesson, '_answer_if_needed') as answer:
            self.lesson._dispatch({'op': 'unlockproblem', 'problem': {'prob': 42, 'pres': 9, 'limit': 5}})
        self.assertEqual(answer.call_args.kwargs['observed_at'], received)
        self.assertLess(answer.call_args.kwargs['observed_at'] + answer.call_args.args[1], clock['now'])

    def test_reconcile_distinguishes_untimed_and_expired(self):
        self.lesson._problems[42] = dict(self.problem)
        with patch.object(self.lesson, '_load_presentations'), patch.object(self.lesson, '_answer_if_needed') as answer:
            self.lesson._reconcile_unlocked({'unlockedproblem': [42]})
            self.assertEqual(answer.call_args.args[1], -1)
            self.lesson._problems[42].update(limit=1, sendTime=(time.time()-10)*1000)
            self.lesson._reconcile_unlocked({'unlockedproblem': [42]})
            self.assertEqual(answer.call_args.args[1], 0)
            self.lesson._problems[42].update(limit='0', sendTime=(time.time()-10)*1000)
            self.lesson._reconcile_unlocked({'unlockedproblem': [42]})
            self.assertEqual(answer.call_args.args[1], -1)

    def test_course_disabled_during_refresh_prevents_post(self):
        def fetch(*a, **k):
            self.lesson.course_config['course_enabled'] = False
            self.lesson._problems[42] = dict(self.problem)
            return {42: dict(self.problem)}
        with patch.object(self.lesson, '_fetch_presentation', side_effect=fetch), \
             patch('lesson.http_request') as request:
            self.lesson._auto_submit(self.quiz['id'], self.problem, ['A'], self.result, None)
        request.assert_not_called()
        self.assertEqual(self.status(), 'NEEDS_MANUAL')
        self.assertEqual(store.get('a', self.quiz['id'])['error_code'], 'AUTO_OPEN_UNVERIFIED')

    def test_recovery_of_interrupted_submission_remains_visible_and_nonretryable(self):
        self.assertTrue(store.transition('a', self.quiz['id'], ('SUGGESTING',), 'SUBMITTING'))
        store.recover_interrupted()
        self.assertEqual(self.status(), 'FAILED')
        self.assertEqual(store.list_pending('a')[0]['status'], 'FAILED')
        self.assertEqual(store.list_pending('a')[0]['error_code'], 'SUBMISSION_UNKNOWN_CHECK_YUKETANG')
        self.assertFalse(store.transition('a', self.quiz['id'], ('SUGGESTING',), 'SUBMITTING'))
        self.assertTrue(store.mark_answered('a', 1, 42))
        self.assertEqual(self.status(), 'ALREADY_ANSWERED')

    def test_long_near_deadline_wait_aborts_when_unlock_evidence_stale(self):
        self.lesson.course_config['answer_near_deadline'] = True
        now = {'wall': time.time(), 'mono': time.monotonic()}
        self.lesson._unlocked_seen['42'] = now['mono']
        def tick(seconds):
            now['wall'] += seconds
            now['mono'] += seconds
        with patch('lesson.time.time', side_effect=lambda: now['wall']), \
             patch('lesson.time.monotonic', side_effect=lambda: now['mono']), \
             patch.object(self.lesson._stop_event, 'wait', side_effect=tick):
            request = self.run_auto(deadline=now['wall'] + 120)
        self.assertEqual(request.call_count, 0)
        self.assertEqual(self.status(), 'NEEDS_MANUAL')
        self.assertTrue(any(d.get('message') == 'AUTO_OPEN_UNVERIFIED' for _, d in self.events))

    def test_complete_live_event_flow_with_simulated_services(self):
        problem = {**self.problem, 'problemId': 77}
        self.lesson._problems[77] = dict(problem)
        wire_problem = {key: value for key, value in problem.items() if not key.startswith('_')}
        def fake_http(method, url, **kwargs):
            if method == 'GET':
                return SimpleNamespace(status_code=200, json=lambda: {'code': 0, 'data': {'slides': [
                    {'id': 11, 'cover': '', 'problem': dict(wire_problem)}]}})
            self.assertEqual(method, 'POST')
            return SimpleNamespace(status_code=200, json=lambda: {'code': 0})
        fake_answer = {'answer': ['A'], **self.result}
        with patch('ai_gateway.suggest', return_value=fake_answer), patch('lesson.http_request', side_effect=fake_http) as http:
            self.lesson._dispatch({'op': 'unlockproblem', 'problem': {'prob': 77, 'pres': 9, 'limit': 60}})
            end = time.monotonic() + 5
            while time.monotonic() < end:
                item = store.get_by_problem('a', 1, 77)
                if item and item['status'] not in ('NEEDS_MANUAL', 'SUGGESTING', 'SUBMITTING'):
                    break
                time.sleep(.01)
            self.assertEqual(item['status'], 'SUBMITTED')
            self.assertEqual([call.args[0] for call in http.call_args_list], ['GET', 'POST'])


if __name__ == '__main__':
    unittest.main()
