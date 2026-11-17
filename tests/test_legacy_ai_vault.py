import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
root = tempfile.TemporaryDirectory()
os.environ['YUKETANG_STORE_DIR'] = root.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import config
class LegacyVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.patcher = patch.object(config, '_CONFIG_PATH', Path(self.tmp.name) / 'config.json')
        self.patcher.start(); self.addCleanup(self.patcher.stop)
    def old_config(self):
        return {'schema_version':2,'accounts':{'a':{'ai': {'keys':[{'name':'old','provider':'qwen','key':'fake-secret'}], 'active_key':0}}}}
    def test_migrate_and_recover(self):
        config._CONFIG_PATH.write_text(json.dumps(self.old_config()), encoding='utf-8')
        result = config.get_config()
        self.assertTrue(result['accounts']['a']['ai']['keys'][0]['key'].startswith('vault:'))
        self.assertNotIn('fake-secret', config._CONFIG_PATH.read_text(encoding='utf-8'))
        self.assertEqual(config.get_ai_config('a')['keys'][0]['key'], 'fake-secret')
        self.assertEqual(json.loads(config._CONFIG_PATH.with_name('config.pre-vault.json').read_text(encoding='utf-8')), self.old_config())
    def test_crypto_failure_preserves_config(self):
        original = json.dumps(self.old_config()); config._CONFIG_PATH.write_text(original,encoding='utf-8')
        with patch('credentials.dpapi', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError): config.get_config()
        self.assertEqual(config._CONFIG_PATH.read_text(encoding='utf-8'), original)

if __name__ == '__main__': unittest.main()
