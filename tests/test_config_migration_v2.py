import json, os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
_tmp = tempfile.TemporaryDirectory()
os.environ['YUKETANG_STORE_DIR'] = _tmp.name
import config

class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old = config._CONFIG_PATH
        config._CONFIG_PATH = Path(self.temp.name) / 'config.json'
        self.addCleanup(setattr, config, '_CONFIG_PATH', self.old)

    def test_migrate_and_backup(self):
        old = {'accounts': {'a': {'sessionid': 'sentinel', 'courses': {'1': {'type1': 'random', 'type2': 'ai'}}, 'pushdeer': {'language': 'en'}}}}
        original = json.dumps(old)
        config._CONFIG_PATH.write_text(original, encoding='utf-8')
        result = config.get_config()
        self.assertEqual(result['accounts']['a']['sessionid'], 'sentinel')
        self.assertEqual(result['accounts']['a']['courses']['1']['type1'], 'notify_only')
        self.assertEqual(result['accounts']['a']['courses']['1']['type2'], 'ai_suggest_confirm')
        self.assertEqual(result['accounts']['a']['pushdeer']['language'], 'en')
        self.assertEqual(config._CONFIG_PATH.with_name('config.pre-v2.json').read_text(encoding='utf-8'), original)

    def test_invalid_is_unchanged(self):
        config._CONFIG_PATH.write_text('{invalid', encoding='utf-8')
        with self.assertRaises(json.JSONDecodeError): config.get_config()
        self.assertEqual(config._CONFIG_PATH.read_text(encoding='utf-8'), '{invalid')

if __name__ == '__main__': unittest.main()
