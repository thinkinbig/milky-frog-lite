from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from milky_frog.adapters.local import LocalSandbox
from milky_frog.harness.tools import ToolContext
from milky_frog.harness.tools.builtins import fetch as fetch_mod
from milky_frog.harness.tools.builtins.fetch import FetchTool, _BlockedHostError


def _context(workspace: Path) -> ToolContext:
    return ToolContext("run-1", workspace, sandbox=LocalSandbox(workspace))


# ── Pure guards: the security-critical logic ──────────────────────────────


def test_is_public_ip_accepts_global_addresses() -> None:
    import ipaddress

    assert fetch_mod._is_public_ip(ipaddress.ip_address("8.8.8.8"))
    assert fetch_mod._is_public_ip(ipaddress.ip_address("2001:4860:4860::8888"))
    assert fetch_mod._is_public_ip(ipaddress.ip_address("::ffff:8.8.8.8"))


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback
        "10.0.0.1",  # private
        "192.168.1.1",  # private
        "169.254.0.1",  # link-local
        "::1",  # loopback v6
        "fc00::1",  # unique-local v6
        "::ffff:127.0.0.1",  # ipv4-mapped loopback
        "224.0.0.1",  # multicast
        "0.0.0.0",  # unspecified
    ],
)
def test_is_public_ip_rejects_non_public(address: str) -> None:
    import ipaddress

    assert not fetch_mod._is_public_ip(ipaddress.ip_address(address))


def test_guard_host_blocks_loopback_literal() -> None:
    with pytest.raises(_BlockedHostError, match="non-public"):
        fetch_mod._guard_host("127.0.0.1")


def test_validate_request_url_rejects_non_http_schemes() -> None:
    for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://x"):
        with pytest.raises(_BlockedHostError, match="scheme"):
            fetch_mod._validate_request_url(url)


def test_validate_request_url_rejects_missing_host() -> None:
    with pytest.raises(_BlockedHostError, match="no host"):
        fetch_mod._validate_request_url("http:///just/a/path")


def test_check_permitted_redirect_allows_same_origin_and_www() -> None:
    fetch_mod._check_permitted_redirect("http://a.com/x", "http://a.com/y")
    fetch_mod._check_permitted_redirect("http://a.com/x", "http://www.a.com/y")


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("http://a.com/x", "http://b.com/x"),  # cross host
        ("http://a.com/x", "https://a.com/x"),  # scheme change
        ("http://a.com/x", "http://a.com:8080/x"),  # port change
        ("http://a.com/x", "http://user:pw@a.com/x"),  # credentials
    ],
)
def test_check_permitted_redirect_refuses_cross_origin(source: str, target: str) -> None:
    with pytest.raises(_BlockedHostError):
        fetch_mod._check_permitted_redirect(source, target)


def test_html_to_text_strips_markup_and_scripts() -> None:
    html = (
        "<html><head><style>h1{color:red}</style></head>"
        "<body><h1>Title</h1><script>steal()</script>"
        "<p>First&nbsp;paragraph</p><div>Second</div></body></html>"
    )
    text = fetch_mod._html_to_text(html)

    assert "Title" in text
    assert "First\xa0paragraph" in text or "First paragraph" in text
    assert "Second" in text
    assert "steal()" not in text
    assert "color:red" not in text


# ── Tool-level: SSRF/scheme blocking without any network ───────────────────


async def test_fetch_empty_url_is_error(tmp_path: Path) -> None:
    result = await FetchTool().execute(_context(tmp_path), FetchTool.input_model(url="   "))
    assert result.is_error
    assert "empty" in result.content


async def test_fetch_blocks_loopback(tmp_path: Path) -> None:
    result = await FetchTool().execute(
        _context(tmp_path), FetchTool.input_model(url="http://127.0.0.1:9/")
    )
    assert result.is_error
    assert "non-public" in result.content


async def test_fetch_rejects_file_scheme(tmp_path: Path) -> None:
    result = await FetchTool().execute(
        _context(tmp_path), FetchTool.input_model(url="file:///etc/passwd")
    )
    assert result.is_error
    assert "scheme" in result.content


# ── Integration against a local server (SSRF guard stubbed to allow loopback) ──


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/html":
            body = (
                b"<html><head><style>x{}</style></head><body>"
                b"<h1>Hi</h1><script>bad()</script><p>Para</p></body></html>"
            )
            self._respond(200, "text/html; charset=utf-8", body)
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/html")
            self.end_headers()
        elif self.path == "/offsite":
            self.send_response(302)
            self.send_header("Location", "http://example.com/")
            self.end_headers()
        elif self.path == "/notfound":
            self._respond(404, "text/plain", b"nope")
        else:
            self._respond(200, "text/plain", b"plain body")

    def _respond(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # silence test noise
        return


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    # The SSRF guard blocks loopback by design; stub it so the transport,
    # redirect, and parsing logic can be exercised against a local server.
    monkeypatch.setattr(fetch_mod, "_guard_host", lambda host: None)
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        thread.join()


async def test_fetch_html_returns_wrapped_text(tmp_path: Path, server: str) -> None:
    result = await FetchTool().execute(
        _context(tmp_path), FetchTool.input_model(url=f"{server}/html")
    )
    assert not result.is_error
    assert "-> 200" in result.content
    assert "<untrusted-external-content>" in result.content
    assert "UNTRUSTED" in result.content
    assert "Hi" in result.content
    assert "Para" in result.content
    assert "bad()" not in result.content


async def test_fetch_follows_same_origin_redirect(tmp_path: Path, server: str) -> None:
    result = await FetchTool().execute(
        _context(tmp_path), FetchTool.input_model(url=f"{server}/redirect")
    )
    assert not result.is_error
    assert "redirected from" in result.content
    assert "Hi" in result.content


async def test_fetch_refuses_cross_origin_redirect(tmp_path: Path, server: str) -> None:
    result = await FetchTool().execute(
        _context(tmp_path), FetchTool.input_model(url=f"{server}/offsite")
    )
    assert result.is_error
    assert "cross-origin" in result.content


async def test_fetch_http_error_status_is_error(tmp_path: Path, server: str) -> None:
    result = await FetchTool().execute(
        _context(tmp_path), FetchTool.input_model(url=f"{server}/notfound")
    )
    assert result.is_error
    assert "-> 404" in result.content
    assert "nope" in result.content


def test_fetch_tool_requires_approval() -> None:
    assert FetchTool().requires_approval is True


# ── Jina Reader fallback ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "text"),
    [
        (403, "forbidden"),
        (429, "too many requests"),
        (503, "service unavailable"),
        (999, "blocked"),
        (200, "Just a moment... Checking your browser before accessing"),
        (200, "Please verify you are human to continue"),
    ],
)
def test_looks_blocked_detects_bot_defenses(status: int, text: str) -> None:
    assert fetch_mod._looks_blocked(status, text)


@pytest.mark.parametrize(("status", "text"), [(200, "normal page body"), (404, "not found")])
def test_looks_blocked_ignores_normal_responses(status: int, text: str) -> None:
    assert not fetch_mod._looks_blocked(status, text)


async def test_fetch_without_jina_key_does_not_retry_blocked_response(
    tmp_path: Path, server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_handler(self: _Handler) -> None:
        self._respond(403, "text/plain", b"forbidden")

    monkeypatch.setattr(_Handler, "do_GET", fake_handler)

    result = await FetchTool().execute(_context(tmp_path), FetchTool.input_model(url=server))

    assert result.is_error
    assert "-> 403" in result.content
    assert "Jina" not in result.content


async def test_fetch_with_jina_key_retries_blocked_response(
    tmp_path: Path, server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_handler(self: _Handler) -> None:
        self._respond(403, "text/plain", b"forbidden")

    monkeypatch.setattr(_Handler, "do_GET", fake_handler)

    def fake_jina(url: str, api_key: str, timeout: float) -> tuple[int, str]:
        assert api_key == "jina-key"
        assert timeout > 0  # retry gets the remaining budget, not a fresh timeout
        return 200, "clean rendered content"

    monkeypatch.setattr(fetch_mod, "_fetch_via_jina", fake_jina)

    result = await FetchTool(jina_api_key="jina-key").execute(
        _context(tmp_path), FetchTool.input_model(url=server)
    )

    assert not result.is_error
    assert "-> 200" in result.content
    assert "blocked; retried via Jina Reader" in result.content
    assert "clean rendered content" in result.content
    # The type line reflects Jina's markdown, not the blocked page's old type.
    assert "content-type: text/markdown" in result.content


async def test_fetch_jina_fallback_failure_returns_original_blocked_response(
    tmp_path: Path, server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_handler(self: _Handler) -> None:
        self._respond(403, "text/plain", b"forbidden")

    monkeypatch.setattr(_Handler, "do_GET", fake_handler)

    def failing_jina(url: str, api_key: str, timeout: float) -> tuple[int, str]:
        raise TimeoutError

    monkeypatch.setattr(fetch_mod, "_fetch_via_jina", failing_jina)

    result = await FetchTool(jina_api_key="jina-key").execute(
        _context(tmp_path), FetchTool.input_model(url=server)
    )

    assert result.is_error
    assert "-> 403" in result.content
    assert "Jina" not in result.content


async def test_fetch_jina_redirect_falls_back_to_original_blocked_response(
    tmp_path: Path, server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_handler(self: _Handler) -> None:
        self._respond(403, "text/plain", b"forbidden")

    monkeypatch.setattr(_Handler, "do_GET", fake_handler)

    def redirecting_jina(url: str, api_key: str, timeout: float) -> tuple[int, str]:
        raise fetch_mod.JinaRedirectError("Jina responded with an unexpected redirect (302)")

    monkeypatch.setattr(fetch_mod, "_fetch_via_jina", redirecting_jina)

    result = await FetchTool(jina_api_key="jina-key").execute(
        _context(tmp_path), FetchTool.input_model(url=server)
    )

    assert result.is_error
    assert "-> 403" in result.content
    assert "Jina" not in result.content


async def test_fetch_jina_error_status_falls_back_to_original_blocked_response(
    tmp_path: Path, server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_handler(self: _Handler) -> None:
        self._respond(403, "text/plain", b"forbidden")

    monkeypatch.setattr(_Handler, "do_GET", fake_handler)

    def erroring_jina(url: str, api_key: str, timeout: float) -> tuple[int, str]:
        return 502, "upstream exploded"  # Jina reached but failed to fetch

    monkeypatch.setattr(fetch_mod, "_fetch_via_jina", erroring_jina)

    result = await FetchTool(jina_api_key="jina-key").execute(
        _context(tmp_path), FetchTool.input_model(url=server)
    )

    assert result.is_error
    assert "-> 403" in result.content  # original block, not Jina's 502
    assert "Jina" not in result.content


async def test_fetch_does_not_retry_a_clean_200_response(
    tmp_path: Path, server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_jina(url: str, api_key: str, timeout: float) -> tuple[int, str]:
        raise AssertionError("should not be called for a clean 200 response")

    monkeypatch.setattr(fetch_mod, "_fetch_via_jina", unexpected_jina)

    result = await FetchTool(jina_api_key="jina-key").execute(
        _context(tmp_path), FetchTool.input_model(url=f"{server}/html")
    )

    assert not result.is_error
