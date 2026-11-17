import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

_store = tempfile.TemporaryDirectory()
os.environ['YUKETANG_STORE_DIR'] = _store.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from monitor import Monitor
from lesson import Lesson

class MonitorHealthTests(unittest.TestCase):
    def test_lesson_health_is_not_browser_health(self):
        monitor = Monitor('account', asyncio.Queue())
        monitor._health_update(state='CONNECTING', last_poll_success_at='2026-01-01T00:00:00+00:00')
        self.assertEqual(monitor.health()['state'], 'IDLE')
        lesson = Lesson('account', {'lessonid': 1, 'classroomid': 2, 'lessonname': 'Class'}, 'x', 'pro.yuketang.cn', {}, lambda *_: None)
        monitor._active_lessons[1] = lesson
        self.assertEqual(monitor.health()['state'], 'RECOVERING')
        sock = Mock()
        lesson._on_open(sock)
        self.assertEqual(monitor.health()['state'], 'CONNECTED')
        self.assertIsNotNone(monitor.health()['lessons'][0]['last_connected_at'])
        monitor._health_update(state='DEGRADED', last_error_code='POLL_FAILED')
        self.assertEqual(monitor.health()['state'], 'DEGRADED')

    def test_reconnect_wakes_and_closes_current_socket(self):
        monitor = Monitor('account', asyncio.Queue())
        lesson = Mock()
        lesson.wsapp = Mock()
        monitor._active_lessons[1] = lesson
        monitor.reconnect()
        lesson.wsapp.close.assert_called_once()
        self.assertTrue(monitor._wake_event.is_set())

    def test_transient_errors_do_not_expire_session(self):
        monitor = Monitor('account', asyncio.Queue(), Mock())
        monitor._running = True
        def stop_after_wait(*args, **kwargs):
            monitor._running = False
        with patch.object(monitor, '_current_credentials', return_value=('pro.yuketang.cn', 'x')), \
             patch('monitor.http_request', side_effect=OSError('offline')), \
             patch('monitor.get_poll_interval', return_value=1), \
             patch.object(monitor._wake_event, 'wait', side_effect=stop_after_wait):
            monitor._run()
        self.assertEqual(monitor.health()['state'], 'DEGRADED')
        monitor._on_session_expired.assert_not_called()

    def test_only_explicit_repeated_auth_rejection_expires(self):
        callback = Mock()
        monitor = Monitor('account', asyncio.Queue(), callback)
        monitor._running = True
        response = Mock(status_code=401)
        with patch.object(monitor, '_current_credentials', return_value=('pro.yuketang.cn', 'x')), \
             patch('monitor.http_request', return_value=response), \
             patch('monitor.get_poll_interval', return_value=0), \
             patch('monitor.pushdeer.send_session_expired'), \
             patch('monitor.event_log.append'):
            monitor._run()
        callback.assert_called_once_with('account')
        self.assertEqual(monitor.health()['state'], 'SESSION_EXPIRED')

    def test_forbidden_is_not_treated_as_expired_login(self):
        callback = Mock()
        monitor = Monitor('account', asyncio.Queue(), callback)
        monitor._running = True
        def stop(*_args, **_kwargs): monitor._running = False
        with patch.object(monitor, '_current_credentials', return_value=('pro.yuketang.cn', 'x')), \
             patch('monitor.http_request', return_value=Mock(status_code=403)), \
             patch('monitor.get_poll_interval', return_value=1), \
             patch.object(monitor._wake_event, 'wait', side_effect=stop):
            monitor._run()
        callback.assert_not_called()
        self.assertEqual(monitor.health()['state'], 'DEGRADED')
        self.assertEqual(monitor.health()['last_error_code'], 'ACCESS_DENIED')

    def test_short_greeting_connections_back_off(self):
        lesson = Lesson('test', {'lessonid': 1, 'classroomid': 2, 'lessonname': 'Class'}, 'x', 'pro.yuketang.cn', {}, lambda *_: None)
        delays = []
        class FakeSocket:
            def __init__(self, **kwargs): self.callbacks = kwargs
            def send(self, message): pass
            def close(self): pass
            def run_forever(self, **kwargs):
                self.callbacks['on_open'](self)
                self.callbacks['on_message'](self, '{"op":"heartbeat"}')
        def wait(delay):
            delays.append(delay)
            if len(delays) == 6: lesson.stop_lesson()
        with patch.object(lesson, '_checkin'), patch('lesson.websocket.WebSocketApp', FakeSocket), \
             patch('lesson.random.uniform', return_value=1.0), patch.object(lesson._stop_event, 'wait', side_effect=wait):
            lesson.start_lesson()
        self.assertEqual(delays, [1, 2, 4, 8, 16, 30])
        self.assertEqual(lesson.ws_health['consecutive_failures'], 6)

    def test_reconnect_hello_reconciles_server_snapshot(self):
        lesson = Lesson('test', {'lessonid': 1, 'classroomid': 2, 'lessonname': 'Class'}, 'x', 'pro.yuketang.cn', {}, lambda *_: None)
        problem = {'problemId': 'p1', 'problemType': 1, 'result': None}
        def load(_): lesson._problems['p1'] = problem
        with patch.object(lesson, '_load_presentations', side_effect=load), \
             patch.object(lesson, '_answer_if_needed') as answer:
            lesson._dispatch({'op': 'hello', 'timeline': [], 'presentation': 'x', 'unlockedproblem': ['p1']})
        answer.assert_called_once()
        self.assertEqual(answer.call_args.args[0], problem)

    def test_event_sequence_survives_cache_reset(self):
        import event_log
        with tempfile.TemporaryDirectory() as tmp, patch.object(event_log, '_LOG_DIR', Path(tmp)):
            first = event_log.append('sequence-test', {'type': 'first'})
            event_log._event_sequences.pop('sequence-test')
            second = event_log.append('sequence-test', {'type': 'second'})
            self.assertEqual((first['event_seq'], second['event_seq']), (1, 2))
            self.assertNotEqual(first['event_id'], second['event_id'])
            self.assertEqual(second['account_id'], 'sequence-test')
            event_log._event_sequences.pop('sequence-test')

    def test_browser_websocket_receives_initial_health_snapshot(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from routers import events
        app = FastAPI()
        app.include_router(events.router)
        fake_monitor = Mock()
        fake_monitor.health.return_value = {'account_id': 'account', 'state': 'IDLE', 'lessons': []}
        with patch.object(events, 'account_exists', return_value=True), \
             patch.object(events.state, 'get_monitor', return_value=fake_monitor), \
             patch.object(events.event_log, 'load_recent', return_value=[]), \
             TestClient(app) as client:
            with client.websocket_connect('/ws/accounts/account/events') as ws:
                snapshot = ws.receive_json()
                self.assertEqual(snapshot['type'], 'monitor_health_snapshot')
                self.assertEqual(snapshot['health']['state'], 'IDLE')

if __name__ == '__main__':
    unittest.main()
