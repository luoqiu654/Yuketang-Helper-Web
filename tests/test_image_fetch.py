import unittest
from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from image_fetch import fetch_image, ImageFetchError
class FakeResponse:
    status=200
    def getheader(self, key, default=None): return {'Content-Type':'image/png', 'Content-Length':'8'}.get(key, default)
    def read(self, n): return b'\x89PNG\r\n\x1a\n'
class FakeConnection:
    def __init__(self, host, address, timeout): self.host,self.address=host,address
    def request(self, method, target, headers):
        assert method=='GET' and target=='/image' and 'Cookie' not in headers
    def getresponse(self): return FakeResponse()
    def close(self): pass
class ImageTest(unittest.TestCase):
    def test_public_pinned_and_no_cookie(self):
        with patch('image_fetch.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]), patch('image_fetch.PinnedHTTPSConnection',FakeConnection):
            self.assertEqual(fetch_image('https://example.com/image')[1],'image/png')
    def test_rejects_private_and_http(self):
        with patch('image_fetch.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ImageFetchError): fetch_image('https://example.com/image')
        with self.assertRaises(ImageFetchError): fetch_image('http://example.com/image')
if __name__=='__main__': unittest.main()
