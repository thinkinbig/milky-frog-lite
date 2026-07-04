from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator

import pytest

from milky_frog.harness.tools._http import guarded_hop


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://elsewhere.example/x")
            self.end_headers()
        elif self.path == "/notfound":
            self._respond(404, b"gone")
        elif self.path == "/huge":
            self._respond(200, b"x" * 5000)
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


_HEADERS = {"User-Agent": "test"}


def test_guarded_hop_returns_status_headers_and_body(server: str) -> None:
    hop = guarded_hop(f"{server}/", headers=_HEADERS, timeout=5, max_bytes=1024)
    assert hop.status == 200
    assert hop.location is None
    assert hop.body == b"ok body"
    assert "Content-Type" in hop.headers


def test_guarded_hop_surfaces_a_redirect_without_following_it(server: str) -> None:
    # The whole point of the guard: a 3xx is reported as data, never chased.
    hop = guarded_hop(f"{server}/redirect", headers=_HEADERS, timeout=5, max_bytes=1024)
    assert hop.status == 302
    assert hop.location == "http://elsewhere.example/x"
    assert hop.body == b""  # a redirect hop carries no content


def test_guarded_hop_returns_4xx_as_a_hop_not_an_exception(server: str) -> None:
    # fetch treats an error status as data and web_search treats it as failure;
    # both read hop.status instead of disagreeing on whether urllib raised.
    hop = guarded_hop(f"{server}/notfound", headers=_HEADERS, timeout=5, max_bytes=1024)
    assert hop.status == 404
    assert hop.body == b"gone"


def test_guarded_hop_caps_the_download(server: str) -> None:
    hop = guarded_hop(f"{server}/huge", headers=_HEADERS, timeout=5, max_bytes=100)
    assert len(hop.body) == 100  # bounded, not the full 5000 bytes
