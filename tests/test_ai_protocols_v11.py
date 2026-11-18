import os, sys, sqlite3, tempfile, unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
from unittest.mock import patch

_tmp = tempfile.TemporaryDirectory()
os.environ['YUKETANG_STORE_DIR'] = _tmp.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from routers import ai_registry as r
from ai_gateway import suggest, GatewayError
from ai_protocols import request_parts, response_text, ProtocolError

class Response:
    status_code=200
    def __init__(self,data): self.data=data
    def json(self): return self.data

class FakeClient:
    requests=[]
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def post(self, url, headers, json):
        self.requests.append((url,headers,json))
        text = 'red 7' if 'Identify the color' in str(json) else 'A'
        if ':generateContent' in url:
            return Response({'candidates':[{'content':{'parts':[{'text':text}]}}]})
        if url.endswith('/responses'):
            return Response({'output':[{'type':'message','content':[{'type':'output_text','text':text}]}]})
        if url.endswith('/messages'):
            return Response({'content':[{'type':'text','text':text}]})
        return Response({'choices':[{'message':{'content':text}}]})
    def get(self,url,headers):
        self.requests.append((url,headers,None))
        return Response({'models':[{'name':'models/gemini-demo'}]} if 'x-goog-api-key' in headers else {'data':[{'id':'demo'}]})

class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory(); self.addCleanup(self.dir.cleanup)
        self.patch=patch.object(r,'DB',Path(self.dir.name)/'registry.sqlite3');self.patch.start();self.addCleanup(self.patch.stop)
        FakeClient.requests=[]
    def test_legacy_database_migrates_to_chat(self):
        with sqlite3.connect(r.DB) as db:
            db.execute('CREATE TABLE providers (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, name TEXT NOT NULL, base_url TEXT NOT NULL, credential BLOB NOT NULL, enabled INTEGER NOT NULL, proxy_mode TEXT NOT NULL)')
            db.execute('INSERT INTO providers VALUES (?,?,?,?,?,?,?)',('p','a','legacy','https://example.test/v1',b'x',1,'system'))
        db.close()
        self.assertEqual(r.providers(account_id='a')[0]['protocol'],'openai_chat_completions')
    def test_existing_protocol_survives_legacy_update(self):
        p=r.create_provider(r.Provider(name='A', base_url='https://example.test/v1', api_key='fake', protocol='anthropic_messages'), account_id='a')
        updated=r.update_provider(p['id'], r.Provider(name='A2',base_url='https://example.test/v1'), account_id='a')
        self.assertEqual(updated['protocol'],'anthropic_messages')
    def test_concurrent_legacy_database_migration(self):
        with sqlite3.connect(r.DB) as db:
            db.execute('CREATE TABLE providers (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, name TEXT NOT NULL, base_url TEXT NOT NULL, credential BLOB NOT NULL, enabled INTEGER NOT NULL, proxy_mode TEXT NOT NULL)')
            db.execute('INSERT INTO providers VALUES (?,?,?,?,?,?,?)', ('p','a','legacy','https://example.test/v1',b'x',1,'system'))
        db.close()
        barrier = Barrier(8)
        def read_provider(_):
            barrier.wait()
            return r.providers(account_id='a')[0]['protocol']
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(list(executor.map(read_provider, range(8))), ['openai_chat_completions'] * 8)
    def test_gemini_vision_test_rejects_url_transport(self):
        p = r.create_provider(r.Provider(name='A', base_url='https://example.test/v1beta', api_key='fake', protocol='gemini_native'), account_id='a')
        m = r.create_model(p['id'], r.Model(model_id='vision', display_name='V', image_transport='url'), account_id='a')
        with patch('routers.ai_registry.httpx.Client', FakeClient):
            result = r.test_model(m['id'], r.TestRequest(test_type='vision', request_id='1'), account_id='a')
        self.assertEqual(result['error_code'], 'IMAGE_UNSUPPORTED')
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(FakeClient.requests)
    def test_protocol_test_and_suggestion(self):
        for i,proto in enumerate(('openai_chat_completions','openai_responses','anthropic_messages','gemini_native')):
            aid=str(i)
            p=r.create_provider(r.Provider(name='A', base_url='https://example.test/v1', api_key='fake', protocol=proto), account_id=aid)
            model=r.create_model(p['id'],r.Model(model_id='vision',display_name='V',image_transport='data_url'),account_id=aid)
            r.set_default(r.DefaultModel(model_id=model['id']),account_id=aid)
            with patch('ai_gateway.httpx.Client',FakeClient), patch('ai_gateway.as_data_url',return_value='data:image/png;base64,YQ=='):
                self.assertEqual(suggest(aid,{'problemType':1,'options':[{'key':'A'}],'_cover':'https://example.test/image.png'})['answer'],['A'])
            with patch('routers.ai_registry.httpx.Client',FakeClient):
                self.assertEqual(r.test_model(model['id'],r.TestRequest(test_type='text',request_id='1'),account_id=aid)['status'],'passed')
                self.assertEqual(r.test_model(model['id'],r.TestRequest(test_type='vision',request_id='2'),account_id=aid)['status'],'passed')
                self.assertTrue(r.discover_models(p['id'],account_id=aid)['models'])
            url,headers,body=FakeClient.requests[-4]
            if proto=='gemini_native': self.assertIn('inline_data',body['contents'][0]['parts'][1]);self.assertIn('x-goog-api-key',headers)
            if proto=='anthropic_messages': self.assertEqual(body['messages'][0]['content'][1]['source']['type'],'base64');self.assertIn('anthropic-version',headers)
    def test_invalid_formats_fail_closed(self):
        with self.assertRaises(ProtocolError): request_parts('gemini_native','https://example.test','m','fake','x','https://example.test/img',32)
        with self.assertRaises(ProtocolError): response_text('gemini_native',{'candidates':[]})
        with self.assertRaises(ValueError): r.Provider(name='A',base_url='https://example.test',protocol='fake')

if __name__=='__main__': unittest.main()
