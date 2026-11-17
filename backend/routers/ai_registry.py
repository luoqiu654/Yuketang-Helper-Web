"""Account-scoped provider registry. Credentials are protected with Windows DPAPI."""
import base64
import ctypes
import json
import struct
import zlib
import sqlite3
import time
import uuid
from contextlib import contextmanager
from ctypes import wintypes
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from config import STORE_DIR
from routers._common import AccountDep

router = APIRouter()
DB = STORE_DIR / 'ai_registry.sqlite3'

class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]

def protect(value: bytes, decrypt=False) -> bytes:
    if not hasattr(ctypes, 'windll'):
        raise RuntimeError('Windows DPAPI is required for credential storage')
    buff = ctypes.create_string_buffer(value)
    src = Blob(len(value), ctypes.cast(buff, ctypes.POINTER(ctypes.c_byte)))
    dst = Blob()
    crypt = ctypes.windll.crypt32
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        ok = fn(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst))
    else:
        ok = fn(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst))
    if not ok:
        raise OSError('DPAPI operation failed')
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.data)

@contextmanager
def connection():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('CREATE TABLE IF NOT EXISTS providers (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, name TEXT NOT NULL, base_url TEXT NOT NULL, credential BLOB NOT NULL, enabled INTEGER NOT NULL, proxy_mode TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS models (id TEXT PRIMARY KEY, provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE, model_id TEXT NOT NULL, display_name TEXT NOT NULL, supports_vision TEXT NOT NULL, timeout_seconds INTEGER NOT NULL, max_output_tokens INTEGER NOT NULL, enabled INTEGER NOT NULL, test_result TEXT)')
    if 'image_transport' not in {r[1] for r in db.execute('PRAGMA table_info(models)')}:
        db.execute("ALTER TABLE models ADD COLUMN image_transport TEXT NOT NULL DEFAULT 'url'")
    db.execute('CREATE TABLE IF NOT EXISTS model_defaults (account_id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE)')
    db.execute('CREATE TABLE IF NOT EXISTS model_tests (model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE, test_type TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY (model_id, test_type))')
    try:
        with db:
            yield db
    finally:
        db.close()

class Provider(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(default='', max_length=4096)
    enabled: bool = True
    proxy_mode: str = 'system'
    @field_validator('base_url')
    @classmethod
    def url(cls, value):
        u = urlsplit(value)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('HTTPS URL without credentials, query or fragment required')
        return value.rstrip('/')
    @field_validator('proxy_mode')
    @classmethod
    def proxy(cls, value):
        if value not in ('system', 'direct'):
            raise ValueError('Invalid proxy mode')
        return value

class Model(BaseModel):
    model_id: str = Field(min_length=1, max_length=300)
    display_name: str = Field(min_length=1, max_length=300)
    supports_vision: str = 'unknown'
    image_transport: str = 'url'
    timeout_seconds: int = Field(default=20, ge=1, le=120)
    max_output_tokens: int = Field(default=128, ge=1, le=8192)
    enabled: bool = True
    @field_validator('image_transport')
    @classmethod
    def transport(cls, value):
        if value not in ('url', 'data_url'):
            raise ValueError('Invalid image transport')
        return value
    @field_validator('supports_vision')
    @classmethod
    def vision(cls, value):
        if value not in ('yes', 'no', 'unknown'):
            raise ValueError('Invalid vision setting')
        return value

def provider(db, account_id, provider_id):
    row = db.execute('SELECT * FROM providers WHERE account_id=? AND id=?', (account_id, provider_id)).fetchone()
    if row is None:
        raise HTTPException(404, 'Provider not found')
    return row

def model(db, account_id, model_id):
    row = db.execute('SELECT m.*, p.account_id, p.base_url, p.credential, p.proxy_mode, p.enabled AS provider_enabled FROM models m JOIN providers p ON p.id=m.provider_id WHERE m.id=? AND p.account_id=?', (model_id, account_id)).fetchone()
    if row is None:
        raise HTTPException(404, 'Model not found')
    return row

def public_provider(row):
    return {k: row[k] for k in ('id','name','base_url','enabled','proxy_mode')} | {'has_key': bool(row['credential']), 'protocol': 'openai_chat_completions'}

def public_model(row):
    return {k: row[k] for k in ('id','provider_id','model_id','display_name','supports_vision','timeout_seconds','max_output_tokens','enabled','image_transport')}

@router.get('/api/accounts/{account_id}/ai/providers')
def providers(account_id: str = AccountDep):
    with connection() as db:
        return [public_provider(row) for row in db.execute('SELECT * FROM providers WHERE account_id=?', (account_id,))]

@router.post('/api/accounts/{account_id}/ai/providers')
def create_provider(body: Provider, account_id: str = AccountDep):
    if not body.api_key:
        raise HTTPException(422, 'API key required')
    pid = str(uuid.uuid4())
    with connection() as db:
        db.execute('INSERT INTO providers VALUES (?,?,?,?,?,?,?)', (pid, account_id, body.name, body.base_url, protect(body.api_key.encode()), body.enabled, body.proxy_mode))
        return public_provider(provider(db, account_id, pid))

@router.patch('/api/accounts/{account_id}/ai/providers/{provider_id}')
def update_provider(provider_id: str, body: Provider, account_id: str = AccountDep):
    with connection() as db:
        old = provider(db, account_id, provider_id)
        key = protect(body.api_key.encode()) if body.api_key else old['credential']
        db.execute('UPDATE providers SET name=?,base_url=?,credential=?,enabled=?,proxy_mode=? WHERE id=?', (body.name, body.base_url, key, body.enabled, body.proxy_mode, provider_id))
        return public_provider(provider(db, account_id, provider_id))

@router.delete('/api/accounts/{account_id}/ai/providers/{provider_id}')
def delete_provider(provider_id: str, account_id: str = AccountDep):
    with connection() as db:
        provider(db, account_id, provider_id)
        db.execute('DELETE FROM providers WHERE id=?', (provider_id,))
    return {'ok': True}

@router.get('/api/accounts/{account_id}/ai/providers/{provider_id}/models')
def models(provider_id: str, account_id: str = AccountDep):
    with connection() as db:
        provider(db, account_id, provider_id)
        return [public_model(r) for r in db.execute('SELECT * FROM models WHERE provider_id=?', (provider_id,))]

@router.post('/api/accounts/{account_id}/ai/providers/{provider_id}/models')
def create_model(provider_id: str, body: Model, account_id: str = AccountDep):
    mid = str(uuid.uuid4())
    with connection() as db:
        provider(db, account_id, provider_id)
        db.execute('INSERT INTO models(id,provider_id,model_id,display_name,supports_vision,timeout_seconds,max_output_tokens,enabled,image_transport) VALUES (?,?,?,?,?,?,?,?,?)', (mid, provider_id, body.model_id, body.display_name, body.supports_vision, body.timeout_seconds, body.max_output_tokens, body.enabled, body.image_transport))
        return public_model(model(db, account_id, mid))

@router.patch('/api/accounts/{account_id}/ai/models/{model_id}')
def update_model(model_id: str, body: Model, account_id: str = AccountDep):
    with connection() as db:
        model(db, account_id, model_id)
        db.execute('UPDATE models SET model_id=?,display_name=?,supports_vision=?,timeout_seconds=?,max_output_tokens=?,enabled=?,image_transport=? WHERE id=?', (body.model_id, body.display_name, body.supports_vision, body.timeout_seconds, body.max_output_tokens, body.enabled, body.image_transport, model_id))
        return public_model(model(db, account_id, model_id))

@router.delete('/api/accounts/{account_id}/ai/models/{model_id}')
def delete_model(model_id: str, account_id: str = AccountDep):
    with connection() as db:
        model(db, account_id, model_id)
        db.execute('DELETE FROM models WHERE id=?', (model_id,))
    return {'ok': True}

def test_image():
    # Tiny generated PNG: red circle and a black seven on white.
    width, height = 128, 80
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            red = (x - 35)**2 + (y - 40)**2 <= 22**2
            seven = (70 <= x <= 109 and 16 <= y <= 21) or (16 <= y <= 60 and abs(x - (105 - (y - 16) * 0.55)) < 3)
            row.extend((255, 0, 0) if red else ((0, 0, 0) if seven else (255, 255, 255)))
        rows.append(bytes(row))
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b''.join(rows))) + chunk(b'IEND', b'')

class TestRequest(BaseModel):
    test_type: str
    timeout_seconds: int = Field(default=20, ge=1, le=120)
    request_id: str = Field(min_length=1, max_length=100)

@router.post('/api/accounts/{account_id}/ai/models/{model_id}/test')
def test_model(model_id: str, body: TestRequest, account_id: str = AccountDep):
    if body.test_type not in ('text','connection','vision'):
        raise HTTPException(422, 'Invalid test type')
    with connection() as db:
        row = model(db, account_id, model_id)
        if not row['enabled'] or not row['provider_enabled']:
            raise HTTPException(409, 'Model or provider disabled')
        start = time.monotonic()
        code = None
        try:
            key = protect(row['credential'], decrypt=True).decode()
            content = 'Reply with the word OK.'
            if body.test_type == 'vision':
                content = [{'type': 'text', 'text': 'Identify the color of the circle and the digit. Reply with just the color and digit.'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(test_image()).decode()}}]
            with httpx.Client(timeout=body.timeout_seconds, trust_env=row['proxy_mode'] == 'system', follow_redirects=False) as client:
                r = client.post(row['base_url'] + '/chat/completions', headers={'Authorization': 'Bearer ' + key}, json={'model': row['model_id'], 'messages': [{'role': 'user', 'content': content}], 'max_tokens': row['max_output_tokens']})
                if r.status_code in (401, 403): code = 'AUTH_FAILED'
                elif r.status_code == 404: code = 'MODEL_NOT_FOUND'
                elif r.status_code == 429: code = 'RATE_LIMITED'
                elif not 200 <= r.status_code < 300: code = 'PROVIDER_UNAVAILABLE'
                else:
                    data = r.json()
                    answer = (data['choices'][0].get('message') or {}).get('content') if data.get('choices') else None
                    if not isinstance(answer, str) or not answer.strip(): code = 'EMPTY_RESPONSE'
                    elif body.test_type == 'vision' and not (('red' in answer.lower() or '红' in answer) and ('7' in answer or 'seven' in answer.lower() or '七' in answer)): code = 'IMAGE_UNSUPPORTED'
        except httpx.TimeoutException: code = 'REQUEST_TIMEOUT'
        except (ValueError, KeyError, TypeError, IndexError): code = 'INVALID_RESPONSE_FORMAT'
        except Exception: code = 'PROVIDER_UNAVAILABLE'
        result = {'status': 'failed' if code else 'passed', 'latency_ms': round((time.monotonic() - start)*1000), 'provider_id': row['provider_id'], 'model_id': model_id, 'error_code': code, 'message': code or 'Response received', 'tested_at': datetime.now(timezone.utc).isoformat(), 'test_type': body.test_type}
        db.execute('UPDATE models SET test_result=? WHERE id=?', (json.dumps(result), model_id))
        db.execute('INSERT OR REPLACE INTO model_tests(model_id,test_type,result) VALUES(?,?,?)', (model_id, body.test_type, json.dumps(result)))
        return result

@router.get('/api/accounts/{account_id}/ai/models/{model_id}/test-result')
def test_result(model_id: str, account_id: str = AccountDep):
    with connection() as db:
        row = model(db, account_id, model_id)
        return json.loads(row['test_result']) if row['test_result'] else None

@router.get('/api/accounts/{account_id}/ai/default')
def get_default(account_id: str = AccountDep):
    with connection() as db:
        row = db.execute('SELECT model_id FROM model_defaults WHERE account_id=?', (account_id,)).fetchone()
        return {'model_id': row['model_id'] if row else None}

class DefaultModel(BaseModel):
    model_id: str

@router.put('/api/accounts/{account_id}/ai/default')
def set_default(body: DefaultModel, account_id: str = AccountDep):
    with connection() as db:
        row = model(db, account_id, body.model_id)
        if not row['enabled'] or not row['provider_enabled']:
            raise HTTPException(409, 'Model or provider disabled')
        db.execute('INSERT OR REPLACE INTO model_defaults VALUES (?,?)', (account_id, body.model_id))
    return {'model_id': body.model_id}

@router.get('/api/accounts/{account_id}/ai/models/{model_id}/test-results')
def test_results(model_id: str, account_id: str = AccountDep):
    with connection() as db:
        model(db, account_id, model_id)
        return {r['test_type']: json.loads(r['result']) for r in db.execute('SELECT test_type,result FROM model_tests WHERE model_id=?', (model_id,))}

@router.post('/api/accounts/{account_id}/ai/providers/{provider_id}/models/discover')
def discover_models(provider_id: str, account_id: str = AccountDep):
    with connection() as db:
        row = dict(provider(db, account_id, provider_id))
    if not row['enabled']:
        raise HTTPException(409, 'Provider disabled')
    try:
        key = protect(row['credential'], decrypt=True).decode()
        with httpx.Client(timeout=15, trust_env=row['proxy_mode'] == 'system', follow_redirects=False) as client:
            response = client.get(row['base_url'] + '/models', headers={'Authorization': 'Bearer ' + key})
        if response.status_code in (401, 403):
            raise HTTPException(502, 'AUTH_FAILED')
        if not 200 <= response.status_code < 300:
            raise HTTPException(502, 'PROVIDER_UNAVAILABLE')
        data = response.json().get('data', [])
        return {'models': [str(item['id']) for item in data if isinstance(item, dict) and isinstance(item.get('id'), str)][:500]}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'PROVIDER_UNAVAILABLE') from None
