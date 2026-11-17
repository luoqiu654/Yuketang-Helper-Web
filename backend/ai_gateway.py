"""Generate suggestions only. This module has no access to the submission API."""
import logging
import re
import time
from urllib.parse import urlsplit
import httpx
from routers.ai_registry import connection, model, protect
from image_fetch import as_data_url, fetch_image, ImageFetchError
from config import get_ai_config

log = logging.getLogger(__name__)

class GatewayError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def suggest(account_id: str, problem: dict) -> dict:
    start = time.monotonic()
    with connection() as db:
        selected = db.execute('SELECT model_id FROM model_defaults WHERE account_id=?', (account_id,)).fetchone()
        row = dict(model(db, account_id, selected['model_id'])) if selected else None
    if row is None:
        cfg = get_ai_config(account_id)
        index = cfg.get('active_key', -1)
        keys = cfg.get('keys', [])
        if not isinstance(index, int) or not 0 <= index < len(keys):
            raise GatewayError('NO_DEFAULT_MODEL')
        legacy = keys[index]
        kind = legacy['provider']
        if kind not in ('google', 'qwen') or not legacy.get('key'):
            raise GatewayError('NO_DEFAULT_MODEL')
        row = {'enabled': True, 'provider_enabled': True, 'provider_id': 'legacy-' + kind,
               'model_id': 'gemini-flash-latest' if kind == 'google' else 'Qwen/Qwen3.8-Flash-Next',
               'base_url': 'https://api-inference.modelscope.cn/v1', 'legacy_key': legacy['key'],
               'supports_vision': 'unknown', 'image_transport': 'url', 'proxy_mode': 'direct',
               'timeout_seconds': 20, 'max_output_tokens': 512, 'protocol': kind}
    if not row['enabled'] or not row['provider_enabled']:
        raise GatewayError('MODEL_DISABLED')
    ptype = problem.get('problemType')
    if ptype not in (1, 2, 3, 5):
        raise GatewayError('UNSUPPORTED_PROBLEM')
    options = [str(option.get('key', '')) for option in problem.get('options', [])]
    if ptype != 5 and (not options or any(not x for x in options) or len(set(options)) != len(options)):
        raise GatewayError('INVALID_OPTIONS')
    url = problem.get('_cover', '')
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise GatewayError('IMAGE_UNAVAILABLE')
    if row['supports_vision'] == 'no':
        raise GatewayError('IMAGE_UNSUPPORTED')
    if row['image_transport'] == 'data_url':
        try: url = as_data_url(url)
        except ImageFetchError: raise GatewayError('IMAGE_FETCH_FAILED') from None
    if ptype == 5:
        instruction = 'Read the question and reply with only a concise nonempty answer.'
    else:
        limit = 1 if ptype == 1 else (min(int(problem.get('pollingCount') or 1), len(options)) if ptype == 3 else len(options))
        instruction = 'Read the question. Reply only with option keys separated by spaces. Valid keys: ' + ', '.join(options) + '. Select at most ' + str(limit) + '.'
    try:
        key = row.get('legacy_key') or protect(row['credential'], decrypt=True).decode()
        if row.get('protocol') == 'google':
            try: image_bytes, mime = fetch_image(problem['_cover'])
            except ImageFetchError: raise GatewayError('IMAGE_FETCH_FAILED') from None
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=row['timeout_seconds'] * 1000))
            response = client.models.generate_content(model=row['model_id'], contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime), instruction])
            content = response.text
        else:
            with httpx.Client(timeout=row['timeout_seconds'], trust_env=row['proxy_mode'] == 'system', follow_redirects=False) as client:
                response = client.post(row['base_url'] + '/chat/completions', headers={'Authorization': 'Bearer ' + key}, json={'model': row['model_id'], 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': instruction}, {'type': 'image_url', 'image_url': {'url': url}}]}], 'max_tokens': row['max_output_tokens']})
            if response.status_code in (401,403): raise GatewayError('AUTH_FAILED')
            if response.status_code == 404: raise GatewayError('MODEL_NOT_FOUND')
            if response.status_code == 429: raise GatewayError('RATE_LIMITED')
            if not 200 <= response.status_code < 300: raise GatewayError('PROVIDER_UNAVAILABLE')
            content = response.json()['choices'][0]['message']['content']
        if not isinstance(content, str) or not content.strip(): raise GatewayError('EMPTY_RESPONSE')
        if ptype == 5:
            answer = content.strip()
            if len(answer) > 4000: raise GatewayError('INVALID_RESPONSE_FORMAT')
        else:
            tokens = [x for x in re.split(r'[,，\s]+', content.strip()) if x]
            option_map = {x.upper(): x for x in options}
            if not tokens or any(t.upper() not in option_map for t in tokens): raise GatewayError('INVALID_RESPONSE_FORMAT')
            answer = [option_map[t.upper()] for t in tokens]
            if len(set(answer)) != len(answer) or len(answer) > limit: raise GatewayError('INVALID_RESPONSE_FORMAT')
        latency = round((time.monotonic() - start) * 1000)
        log.info('AI suggestion provider=%s model=%s latency_ms=%d status=ok', row['provider_id'], row['model_id'], latency)
        return {'answer': answer, 'model_id': row['model_id'], 'provider_id': row['provider_id'], 'latency_ms': latency}
    except GatewayError:
        raise
    except httpx.TimeoutException:
        raise GatewayError('REQUEST_TIMEOUT') from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise GatewayError('INVALID_RESPONSE_FORMAT') from None
    except Exception:
        raise GatewayError('PROVIDER_UNAVAILABLE') from None
