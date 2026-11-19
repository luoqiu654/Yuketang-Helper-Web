"""Opt-in ASGI access control for the embedded Windows desktop window.

The regular development server is untouched.  The desktop launcher wraps the
app with a per-launch capability; the embedded window exchanges it once for a
host-only, HttpOnly session cookie.  Never log the bootstrap URL or capability.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from http.cookies import CookieError, SimpleCookie


_BOOTSTRAP = '/__desktop__/bootstrap/'
_TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9_-]{32,128}$')
_SAFE_METHODS = frozenset(('GET', 'HEAD', 'OPTIONS'))


def wrap_desktop_app(app, port: int, token: str):
    """Restrict an existing ASGI app to one authenticated local desktop session.

    The caller must bind only to 127.0.0.1 and suppress HTTP access logs.  Token
    exchange is deliberately one-use, and should be loaded directly in WebView2.
    This isolates ordinary web origins and unauthenticated local HTTP clients;
    it cannot protect against processes able to inspect this process or the
    user's browser profile.
    """
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('invalid desktop port')
    if not isinstance(token, str) or not _TOKEN_PATTERN.fullmatch(token):
        raise ValueError('invalid desktop bootstrap capability')
    expected_host = f'127.0.0.1:{port}'
    expected_origin = f'http://{expected_host}'
    cookie_name = 'ykt_' + hashlib.sha256(token.encode('ascii')).hexdigest()[:20]
    cookie_value = secrets.token_urlsafe(32)
    bootstrap_used = False

    async def reject(scope, send):
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
            return
        body = b'{"detail":"Desktop access denied"}'
        await send({'type': 'http.response.start', 'status': 403, 'headers': [
            (b'content-type', b'application/json'),
            (b'content-length', str(len(body)).encode('ascii')),
            (b'cache-control', b'no-store'),
            (b'referrer-policy', b'no-referrer'),
        ]})
        await send({'type': 'http.response.body', 'body': body})

    async def protected(scope, receive, send):
        nonlocal bootstrap_used
        kind = scope['type']
        if kind not in ('http', 'websocket'):
            return await app(scope, receive, send)

        if kind == 'http':
            # SameSite cookies are shared by localhost ports; disallow other
            # local web apps from framing this UI and tricking a user into
            # clicking on authenticated controls.  Append CSP rather than
            # replacing the application's own (independent) policy.
            downstream_send = send

            async def send_with_frame_protection(message):
                if message['type'] == 'http.response.start':
                    message = dict(message)
                    response_headers = [(key, value) for key, value in message.get('headers', [])
                                        if key.lower() != b'x-frame-options']
                    response_headers.extend([
                        (b'x-frame-options', b'DENY'),
                        (b'content-security-policy', b"frame-ancestors 'none'"),
                    ])
                    message['headers'] = response_headers
                await downstream_send(message)

            send = send_with_frame_protection

        headers: dict[bytes, list[bytes]] = {}
        for key, value in scope.get('headers', []):
            headers.setdefault(key.lower(), []).append(value)
        host = headers.get(b'host', [])
        origin = headers.get(b'origin', [])
        # Refuse ambiguous headers.  Origin, when present, must be the exact
        # desktop origin (including port); WebSockets always require it.
        if (len(host) != 1 or host[0] != expected_host.encode('ascii')
                or len(origin) > 1
                or (origin and origin[0] != expected_origin.encode('ascii'))
                or (kind == 'websocket' and not origin)):
            return await reject(scope, send)

        path = scope.get('path', '')
        if kind == 'http' and path.startswith(_BOOTSTRAP):
            supplied = path[len(_BOOTSTRAP):]
            valid = bool(_TOKEN_PATTERN.fullmatch(supplied)) and hmac.compare_digest(supplied, token)
            if scope.get('method') != 'GET' or bootstrap_used or not valid or scope.get('query_string'):
                return await reject(scope, send)
            # No await occurs before this assignment, so the event loop cannot
            # accept two successful exchanges for the same capability.
            bootstrap_used = True
            cookie = f'{cookie_name}={cookie_value}; Path=/; HttpOnly; SameSite=Strict'
            await send({'type': 'http.response.start', 'status': 303, 'headers': [
                (b'location', b'/'),
                (b'set-cookie', cookie.encode('ascii')),
                (b'cache-control', b'no-store'),
                (b'referrer-policy', b'no-referrer'),
                (b'content-length', b'0'),
            ]})
            return await send({'type': 'http.response.body', 'body': b''})

        cookies = SimpleCookie()
        try:
            raw_cookie = b'; '.join(headers.get(b'cookie', [])).decode('latin-1')
            cookies.load(raw_cookie)
            value = cookies[cookie_name].value if cookie_name in cookies else ''
            authorized = value.isascii() and hmac.compare_digest(value, cookie_value)
        except (CookieError, TypeError, ValueError):
            authorized = False
        method = scope.get('method', 'GET')
        # Chromium includes Origin for state-changing fetch/form requests.
        # Requiring it prevents same-site cross-port forms from changing data.
        if not authorized or (kind == 'http' and method not in _SAFE_METHODS and not origin):
            return await reject(scope, send)

        return await app(scope, receive, send)

    return protected
