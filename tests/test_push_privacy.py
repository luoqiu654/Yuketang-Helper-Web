import os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
_tmp = tempfile.TemporaryDirectory()
os.environ['YUKETANG_STORE_DIR'] = _tmp.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import pushdeer

class PushPrivacyTests(unittest.TestCase):
    def test_failure_response_does_not_echo_secret(self):
        with patch.object(pushdeer, 'http_request', return_value=SimpleNamespace(json=lambda: {'code': 1, 'error': 'sentinel-secret'})):
            ok, msg = pushdeer._send('https://example.test', 'sentinel-secret', 'test', 'test')
        self.assertFalse(ok)
        self.assertNotIn('sentinel-secret', msg)
    def test_exception_does_not_echo_secret(self):
        with patch.object(pushdeer, 'http_request', side_effect=RuntimeError('sentinel-secret')):
            ok, msg = pushdeer._send('https://example.test', 'sentinel-secret', 'test', 'test')
        self.assertFalse(ok)
        self.assertNotIn('sentinel-secret', msg)

if __name__ == '__main__': unittest.main()
