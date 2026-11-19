"""Desktop authorization checks use a fake ASGI app and never touch user data."""
import secrets
import sys
import unittest
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from desktop_security import wrap_desktop_app


class DesktopSecurityTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()

        @app.get('/')
        def home():
            return {'loaded': True}

        @app.get('/api/private')
        def private():
            return {'ok': True}

        @app.post('/api/change')
        def change():
            return {'changed': True}

        @app.websocket('/ws/events')
        async def events(ws: WebSocket):
            await ws.accept()
            await ws.send_text('ok')
            await ws.close()

        self.token = secrets.token_urlsafe(32)
        self.origin = 'http://127.0.0.1:43123'
        self.app = wrap_desktop_app(app, 43123, self.token)
        self.client = TestClient(self.app, base_url=self.origin)
        self.addCleanup(self.client.close)

    def authorize(self):
        return self.client.get('/__desktop__/bootstrap/' + self.token, follow_redirects=False)

    def test_cookie_required_and_bootstrap_is_one_use(self):
        self.assertEqual(self.client.get('/api/private').status_code, 403)
        self.assertEqual(self.client.get('/__desktop__/bootstrap/' + secrets.token_urlsafe(32)).status_code, 403)
        result = self.authorize()
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers['location'], '/')
        self.assertIn('HttpOnly', result.headers['set-cookie'])
        self.assertIn('SameSite=Strict', result.headers['set-cookie'])
        self.assertEqual(result.headers['referrer-policy'], 'no-referrer')
        self.assertEqual(self.client.get('/api/private').status_code, 200)
        self.assertEqual(self.authorize().status_code, 403)

    def test_refuse_wrong_host_origin_and_cross_origin_mutation(self):
        self.authorize()
        self.assertEqual(self.client.get('/api/private', headers={'host': 'localhost:43123'}).status_code, 403)
        self.assertEqual(self.client.get('/api/private', headers={'origin': 'http://127.0.0.1:43124'}).status_code, 403)
        self.assertEqual(self.client.post('/api/change').status_code, 403)
        self.assertEqual(self.client.post('/api/change', headers={'origin': 'null'}).status_code, 403)
        self.assertEqual(self.client.post('/api/change', headers={'origin': self.origin}).status_code, 200)

    def test_every_http_response_rejects_framing(self):
        denied = self.client.get('/api/private')
        bootstrap = self.authorize()
        home = self.client.get('/')
        for response in (denied, bootstrap, home):
            self.assertEqual(response.headers['x-frame-options'], 'DENY')
            self.assertIn("frame-ancestors 'none'", response.headers['content-security-policy'])

    def test_websocket_requires_cookie_and_same_origin(self):
        websocket_url = 'ws://127.0.0.1:43123/ws/events'
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(websocket_url, headers={'origin': self.origin}):
                pass
        self.authorize()
        for origin in ('http://evil.test', 'http://127.0.0.1:43124'):
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect(websocket_url, headers={'origin': origin}):
                    pass
        with self.client.websocket_connect(websocket_url, headers={'origin': self.origin}) as ws:
            self.assertEqual(ws.receive_text(), 'ok')

    def test_bad_wrapper_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            wrap_desktop_app(self.app, 0, self.token)
        with self.assertRaises(ValueError):
            wrap_desktop_app(self.app, 43123, 'not-strong')


if __name__ == '__main__':
    unittest.main()
