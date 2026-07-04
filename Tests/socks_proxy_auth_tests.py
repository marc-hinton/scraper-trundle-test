#!/usr/bin/python3
# -*- coding: utf-8 -*-

"""
Unit tests for the authenticated HTTP proxy support.

These tests verify that GoogleScraper.socks attaches a
"Proxy-Authorization" header (as read from the proxy credentials
configured for a scrape, e.g. via a proxy file or the mysql proxy
database) when negotiating a CONNECT tunnel through an upstream
HTTP proxy, and that no such header is sent when no credentials
were supplied.

python -m pytest tests/socks_proxy_auth_tests.py
"""

import base64
import importlib.util
import os
import unittest

# Load GoogleScraper.socks directly from its source file. This avoids
# importing the GoogleScraper package's __init__ (and its heavy,
# environment-dependent dependency chain: selenium, sqlalchemy, etc.),
# which is irrelevant for exercising the pure-python socks module.
_socks_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'GoogleScraper', 'socks.py')
_spec = importlib.util.spec_from_file_location('GoogleScraper.socks', _socks_path)
socks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(socks)


class FakeFile:
    """A minimal file-like object used to fake the CONNECT response."""

    def readline(self):
        # socket.makefile() defaults to text mode, so real callers of
        # _negotiate_HTTP receive str lines here.
        return "HTTP/1.1 200 Connection established\r\n"

    def close(self):
        pass


class SocksProxyAuthTestCase(unittest.TestCase):
    """
    python -m pytest tests/socks_proxy_auth_tests.py::SocksProxyAuthTestCase
    """

    def setUp(self):
        self.sock = socks.socksocket()
        self.sent = None

        def fake_sendall(data):
            self.sent = data

        self.sock.sendall = fake_sendall
        self.sock.makefile = lambda: FakeFile()

    def tearDown(self):
        self.sock.close()

    def test_proxy_authorization_header_added_when_credentials_present(self):
        self.sock.proxy = (socks.PROXY_TYPE_HTTP, b'127.0.0.1', 8080, True,
                           b'trundle', b's3cr3t')

        self.sock._negotiate_HTTP('example.com', 443)

        expected_creds = base64.b64encode(b'trundle:s3cr3t')
        self.assertIn(b'Proxy-Authorization: Basic ' + expected_creds, self.sent)

    def test_no_proxy_authorization_header_when_credentials_absent(self):
        self.sock.proxy = (socks.PROXY_TYPE_HTTP, b'127.0.0.1', 8080, True,
                           None, None)

        self.sock._negotiate_HTTP('example.com', 443)

        self.assertNotIn(b'Proxy-Authorization', self.sent)


if __name__ == '__main__':
    unittest.main()
