import os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
root = tempfile.TemporaryDirectory()
os.environ['YUKETANG_STORE_DIR'] = root.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from routers import ai_registry as r

class AIRegistryTest(unittest.TestCase):
    def test_secret_and_accounts_and_multiple_models(self):
        p = r.create_provider(r.Provider(name='provider', base_url='https://example.com/v1', api_key='secret'), account_id='a')
        self.assertNotIn('secret', str(p))
        self.assertEqual(r.protect(r.protect(b'secret'), True), b'secret')
        first = r.create_model(p['id'], r.Model(model_id='one', display_name='One'), account_id='a')
        second = r.create_model(p['id'], r.Model(model_id='two', display_name='Two'), account_id='a')
        self.assertEqual(len(r.models(p['id'], account_id='a')), 2)
        r.update_model(first['id'], r.Model(model_id='changed', display_name='Changed'), account_id='a')
        self.assertEqual(r.models(p['id'], account_id='a')[1]['model_id'], 'two')
        with self.assertRaises(Exception): r.models(p['id'], account_id='b')
        r.delete_provider(p['id'], account_id='a')

    def test_url_validation(self):
        for url in ('http://example.com', 'https://user:pass@example.com', 'https://example.com?key=123'):
            with self.assertRaises(ValueError): r.Provider(name='x', base_url=url, api_key='secret')

if __name__ == '__main__': unittest.main()
