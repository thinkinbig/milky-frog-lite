from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator

import pytest

from milky_frog.harness.tools import jina as jina_mod
from milky_frog.harness.tools.jina import JinaRedirectError, jina_request


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://evil.example/internal")
            self.end_headers()
        elif self.path == "/huge":
            # Two bytes past the cap so the bounded read has something to trim.
            body = b"x" * (jina_mod.MAX_RESPONSE_BYTES + 2)
            self._respond(200, body)
        else:
            self._respond(200, b"ok body")

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # silence test noise
        return


@pytest.fixture
def server() -> Iterator[str]:
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        thread.join()


def test_jina_request_returns_status_and_body(server: str) -> None:
    status, raw = jina_request(f"{server}/", "key", timeout=5)
    assert status == 200
    assert raw == b"ok body"


def test_jina_request_refuses_to_follow_redirect(server: str) -> None:
    # A redirect Location is attacker-influenceable; following it would bypass
    # fetch.py's SSRF guards, so the helper must raise rather than chase it.
    with pytest.raises(JinaRedirectError):
        jina_request(f"{server}/redirect", "key", timeout=5)


def test_jina_request_caps_the_download(server: str) -> None:
    _status, raw = jina_request(f"{server}/huge", "key", timeout=5)
    assert len(raw) == jina_mod.MAX_RESPONSE_BYTES  # bounded, not the full body
