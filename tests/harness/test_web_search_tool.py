from __future__ import annotations

import http.server
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest

from milky_frog.adapters.local import LocalSandbox
from milky_frog.harness.tools import ToolContext
from milky_frog.harness.tools.builtins import web_search as web_search_mod
from milky_frog.harness.tools.builtins.web_search import WebSearchTool


def _context(workspace: Path) -> ToolContext:
    return ToolContext("run-1", workspace, sandbox=LocalSandbox(workspace))


class _Handler(http.server.BaseHTTPRequestHandler):
    last_headers: ClassVar[dict[str, str]] = {}

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        type(self).last_headers = {key: value for key, value in self.headers.items()}
        payload = json.loads(self.rfile.read(length) or b"{}")
        query = payload.get("q", "")
        status = 200
        if query == "empty":
            body = json.dumps({"code": 200, "status": 20000, "data": []}).encode("utf-8")
        elif query == "malformed":
            body = b"not json"
        elif query == "boom":
            status = 502
            body = b'{"error": "upstream exploded"}'
        else:
            body = json.dumps(
                {
                    "code": 200,
                    "status": 20000,
                    "data": [
                        {
                            "title": f"Result {i}",
                            "url": f"https://example.com/{i}",
                            "description": f"snippet {i}",
                            "content": "full page text " * 100,
                        }
                        for i in range(1, 8)
                    ],
                }
            ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # silence test noise
        return


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    monkeypatch.setattr(web_search_mod, "SEARCH_ENDPOINT", url)
    try:
        yield url
    finally:
        httpd.shutdown()
        thread.join()


async def test_web_search_returns_wrapped_snippets(tmp_path: Path, server: str) -> None:
    result = await WebSearchTool(api_key="key").execute(
        _context(tmp_path), WebSearchTool.input_model(query="python")
    )

    assert not result.is_error
    assert "<untrusted-external-content>" in result.content
    assert "UNTRUSTED" in result.content
    assert "Result 1" in result.content
    assert "https://example.com/1" in result.content
    assert "snippet 1" in result.content
    assert "full page text" not in result.content  # snippet only, not full content


async def test_web_search_requests_locate_only_results(tmp_path: Path, server: str) -> None:
    # The X-Respond-With: no-content header keeps Jina from crawling each hit's
    # full page — we only render title/url/snippet, so the crawl is wasted work.
    await WebSearchTool(api_key="key").execute(
        _context(tmp_path), WebSearchTool.input_model(query="python")
    )

    assert _Handler.last_headers.get("X-Respond-With") == "no-content"


async def test_web_search_caps_results_at_max_results(tmp_path: Path, server: str) -> None:
    result = await WebSearchTool(api_key="key").execute(
        _context(tmp_path), WebSearchTool.input_model(query="python", max_results=3)
    )

    assert not result.is_error
    assert "Result 3" in result.content
    assert "Result 4" not in result.content


async def test_web_search_no_results(tmp_path: Path, server: str) -> None:
    result = await WebSearchTool(api_key="key").execute(
        _context(tmp_path), WebSearchTool.input_model(query="empty")
    )

    assert not result.is_error
    assert "no results" in result.content


async def test_web_search_malformed_response_is_error(tmp_path: Path, server: str) -> None:
    result = await WebSearchTool(api_key="key").execute(
        _context(tmp_path), WebSearchTool.input_model(query="malformed")
    )

    assert result.is_error
    assert "malformed" in result.content


async def test_web_search_reports_http_error_status(tmp_path: Path, server: str) -> None:
    # An error status comes back from jina_request as a status, not an
    # exception, so execute reports it and surfaces the response body.
    result = await WebSearchTool(api_key="key").execute(
        _context(tmp_path), WebSearchTool.input_model(query="boom")
    )

    assert result.is_error
    assert "HTTP 502" in result.content
    assert "upstream exploded" in result.content


async def test_web_search_empty_query_is_error(tmp_path: Path) -> None:
    result = await WebSearchTool(api_key="key").execute(
        _context(tmp_path), WebSearchTool.input_model(query="   ")
    )

    assert result.is_error
    assert "empty" in result.content


def test_web_search_tool_requires_approval() -> None:
    assert WebSearchTool(api_key="key").requires_approval is True


def test_web_search_max_results_bounds() -> None:
    with pytest.raises(ValueError):
        WebSearchTool.input_model(query="x", max_results=11)
    with pytest.raises(ValueError):
        WebSearchTool.input_model(query="x", max_results=0)
