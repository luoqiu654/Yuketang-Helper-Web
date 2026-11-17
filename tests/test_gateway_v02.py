import os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

tmp = tempfile.TemporaryDirectory()
os.environ['YUKETANG_STORE_DIR'] = tmp.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from routers import ai_registry as registry
from ai_gateway import suggest, GatewayError
from validation import validation_exception_handler

class FakeResponse:
    status_code = 200
    def __init__(self, answer): self.answer=answer
    def json(self): return {'choices':[{'message':{'content':self.answer}}]}
class FakeClient:
    answer = 'A'
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def post(self, *args, **kwargs): return FakeResponse(self.answer)

class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.provider = registry.create_provider(registry.Provider(name='test', base_url='https://example.org/v1', api_key='fake'), account_id='a')
        self.model = registry.create_model(self.provider['id'], registry.Model(model_id='vision', display_name='Vision'), account_id='a')
        registry.set_default(registry.DefaultModel(model_id=self.model['id']), account_id='a')
    def tearDown(self): registry.delete_provider(self.provider['id'], account_id='a')
    def test_strict_answer_and_no_submission(self):
        problem = {'problemType':1, 'options':[{'key':'A'}, {'key':'B'}], '_cover':'https://example.org/image.png'}
        with patch('ai_gateway.httpx.Client', FakeClient):
            FakeClient.answer = 'A'
            self.assertEqual(suggest('a', problem)['answer'], ['A'])
            for bad in ('A B', 'A A', 'A because'):
                FakeClient.answer = bad
                with self.assertRaises(GatewayError): suggest('a', problem)
    def test_legacy_modelscope_and_google(self):
        problem = {'problemType':1, 'options':[{'key':'A'}, {'key':'B'}], '_cover':'https://example.org/image.png'}
        with registry.connection() as db:
            db.execute('DELETE FROM model_defaults WHERE account_id=?', ('a',))
        with patch('ai_gateway.get_ai_config', return_value={'active_key': 0, 'keys': [{'provider':'qwen','key':'fake'}]}), patch('ai_gateway.httpx.Client', FakeClient):
            FakeClient.answer = 'A'
            self.assertEqual(suggest('a', problem)['provider_id'], 'legacy-qwen')
        from unittest.mock import MagicMock
        from google import genai
        client = MagicMock()
        client.models.generate_content.return_value.text = 'B'
        with patch('ai_gateway.get_ai_config', return_value={'active_key': 0, 'keys': [{'provider':'google','key':'fake'}]}), patch('ai_gateway.fetch_image',return_value=(b'fake', 'image/png')), patch.object(genai, 'Client', return_value=client):
            self.assertEqual(suggest('a', problem)['answer'], ['B'])
    def test_422_does_not_reflect_key(self):
        app = FastAPI()
        app.add_exception_handler(RequestValidationError, validation_exception_handler)
        @app.post('/provider')
        def create(body: registry.Provider): return {}
        fake = 'uniqueSecret' * 500
        response = TestClient(app).post('/provider', json={'name':'x', 'base_url':'https://example.com', 'api_key':fake})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(fake, response.text)

if __name__ == '__main__': unittest.main()
