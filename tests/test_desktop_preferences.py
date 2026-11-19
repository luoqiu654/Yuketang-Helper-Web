"""Desktop preferences are private to a data directory and session."""
import json
import secrets
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from desktop_preferences import wrap_desktop_preferences
from desktop_security import wrap_desktop_app


class DesktopPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = Path(self.folder.name)
        self.origin = 'http://127.0.0.1:43125'
        self.token = secrets.token_urlsafe(32)

    def client(self):
        app = FastAPI()

        @app.get('/api/other')
        def unrelated():
            return {'ok': True}

        wrapped = wrap_desktop_app(wrap_desktop_preferences(app, self.store), 43125, self.token)
        client = TestClient(wrapped, base_url=self.origin)
        self.addCleanup(client.close)
        return client

    def authorize(self, client):
        response = client.get('/__desktop__/bootstrap/' + self.token, follow_redirects=False)
        self.assertEqual(response.status_code, 303)

    def test_requires_session_and_exact_origin(self):
        client = self.client()
        self.assertEqual(client.get('/api/desktop/preferences').status_code, 403)
        self.assertEqual(client.put('/api/desktop/preferences', json={'language': 'en'}).status_code, 403)
        self.authorize(client)
        self.assertEqual(client.put('/api/desktop/preferences', json={'language': 'en'}).status_code, 403)
        self.assertEqual(client.put('/api/desktop/preferences', json={'language': 'en'},
                                    headers={'origin': 'http://127.0.0.1:43126'}).status_code, 403)
        self.assertEqual(client.get('/api/other').json(), {'ok': True})

    def test_language_is_validated_and_persists_across_desktop_sessions(self):
        client = self.client()
        self.authorize(client)
        self.assertEqual(client.get('/api/desktop/preferences').json(), {'language': None})
        for value in ('en', 'zh'):
            response = client.put('/api/desktop/preferences', json={'language': value}, headers={'origin': self.origin})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'language': value})
        for invalid in ({'language': 'fr'}, {'language': None}, {'language': 'en', 'extra': 'x'}):
            self.assertEqual(client.put('/api/desktop/preferences', json=invalid,
                                        headers={'origin': self.origin}).status_code, 422)
        self.assertEqual(json.loads((self.store / 'desktop-preferences.json').read_text(encoding='utf-8')), {'language': 'zh'})
        next_client = self.client()
        self.authorize(next_client)
        self.assertEqual(next_client.get('/api/desktop/preferences').json(), {'language': 'zh'})

    def test_bad_file_does_not_break_application(self):
        (self.store / 'desktop-preferences.json').write_text('{bad', encoding='utf-8')
        client = self.client()
        self.authorize(client)
        self.assertEqual(client.get('/api/desktop/preferences').json(), {'language': None})
        self.assertEqual(client.put('/api/desktop/preferences', json={'language': 'en'},
                                    headers={'origin': self.origin}).status_code, 200)


if __name__ == '__main__':
    unittest.main()
