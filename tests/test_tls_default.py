import os
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from mcp_obsidian.obsidian import Obsidian


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"verified note")

    def log_message(self, *args):
        pass


class TLSDefaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.cert = str(Path(cls.temp.name) / "cert.pem")
        key = str(Path(cls.temp.name) / "key.pem")
        config = Path(cls.temp.name) / "openssl.cnf"
        config.write_text("[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=TLS test\n[ext]\nsubjectAltName=IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\n")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", key, "-out", cls.cert, "-config", str(config)], check=True, capture_output=True)
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.cert, key)
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def client(self, host="127.0.0.1"):
        return Obsidian("dummy-test-key", protocol="https", host=host, port=self.server.server_port)

    def test_untrusted_certificate_is_rejected_by_default(self):
        with patch.dict(os.environ, {"NO_PROXY": "*"}, clear=True):
            with self.assertRaisesRegex(Exception, "CERTIFICATE_VERIFY_FAILED"):
                self.client().get_file_contents("test.md")

    def test_explicit_ca_bundle_allows_trusted_server(self):
        with patch.dict(os.environ, {"NO_PROXY": "*", "REQUESTS_CA_BUNDLE": self.cert}, clear=True):
            self.assertEqual(self.client().get_file_contents("test.md"), "verified note")

    def test_trusted_certificate_with_wrong_hostname_is_rejected(self):
        with patch.dict(os.environ, {"NO_PROXY": "*", "REQUESTS_CA_BUNDLE": self.cert}, clear=True):
            with self.assertRaisesRegex(Exception, "CERTIFICATE_VERIFY_FAILED"):
                self.client("localhost").get_file_contents("test.md")
