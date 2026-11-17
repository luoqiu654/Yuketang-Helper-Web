"""Fetch public HTTPS images with pinned DNS, TLS checks and strict resource limits."""
import base64
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit

class ImageFetchError(Exception): pass

class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname, address, timeout):
        super().__init__(hostname, 443, timeout=timeout, context=ssl.create_default_context())
        self.address = address
    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise

def fetch_image(url: str, timeout: int = 5) -> tuple[bytes, str]:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.fragment):
            raise ImageFetchError('invalid image URL')
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
            raise ImageFetchError('image host is not public')
        target = parsed.path or '/'
        if parsed.query: target += '?' + parsed.query
        conn = PinnedHTTPSConnection(parsed.hostname, sorted(addresses)[0], timeout)
        try:
            conn.request('GET', target, headers={'Accept': 'image/png,image/jpeg,image/webp,image/gif'})
            response = conn.getresponse()
            if response.status != 200: raise ImageFetchError('image fetch failed')
            mime = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
            if mime not in ('image/png', 'image/jpeg', 'image/webp', 'image/gif'): raise ImageFetchError('unsupported image type')
            length = response.getheader('Content-Length')
            if length and int(length) > 5_000_000: raise ImageFetchError('image too large')
            body = response.read(5_000_001)
            if len(body) > 5_000_000: raise ImageFetchError('image too large')
            signatures = {'image/png': body.startswith(b'\x89PNG\r\n\x1a\n'), 'image/jpeg': body.startswith(b'\xff\xd8\xff'), 'image/gif': body.startswith((b'GIF87a',b'GIF89a')), 'image/webp': body.startswith(b'RIFF') and body[8:12] == b'WEBP'}
            if not signatures[mime]: raise ImageFetchError('image format mismatch')
            return body, mime
        finally:
            conn.close()
    except ImageFetchError:
        raise
    except Exception:
        raise ImageFetchError('image fetch failed') from None

def as_data_url(url: str) -> str:
    data, mime = fetch_image(url)
    return 'data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii')
