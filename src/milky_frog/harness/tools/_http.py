from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import override
from urllib import error as urllib_error
from urllib import request as urllib_request

# 3xx codes that carry a Location we must never chase automatically.
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass(frozen=True, slots=True)
class HttpHop:
    """The outcome of a single guarded HTTP request.

    ``location`` is set only for a redirect status; ``body`` is empty in that
    case (a hop that redirects carries no content we want). Callers decide what
    to do with a redirect — this type carries the facts, not the policy.
    """

    status: int
    headers: Mapping[str, str]
    body: bytes
    location: str | None


class _NoRedirectHandler(urllib_request.HTTPRedirectHandler):
    """Disable urllib's automatic redirect following so callers can vet each hop."""

    @override
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _status_of(response: object) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = getattr(response, "code", 0)
    return int(status or 0)


def guarded_hop(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: Mapping[str, str],
    timeout: float,
    max_bytes: int,
) -> HttpHop:
    """One HTTP request with the two guards every caller needs, and no policy.

    Auto-redirect is disabled (a redirect surfaces as ``HttpHop.location`` for
    the caller to accept or refuse — never followed here) and the body read is
    bounded at ``max_bytes`` so a hostile response cannot exhaust memory. A
    4xx/5xx is returned as an ``HttpHop`` with that status, not raised: callers
    that treat an error status as data (fetch) and callers that treat it as a
    failure (web_search) both read the same field instead of disagreeing on
    whether urllib raised.

    These are exactly the invariants that drifted between fetch and Jina when
    each rewrote the transport by hand; keeping them here means a fix lands once.
    Redirect *policy* (refuse vs. revalidate-and-follow) stays with the caller.
    """
    opener = urllib_request.build_opener(_NoRedirectHandler())
    request = urllib_request.Request(url, data=data, headers=dict(headers), method=method)
    try:
        response = opener.open(request, timeout=timeout)
    except urllib_error.HTTPError as http_error:
        response = http_error
    try:
        status = _status_of(response)
        is_redirect = status in REDIRECT_STATUSES
        location = response.headers.get("Location") if is_redirect else None
        body = b"" if is_redirect else response.read(max_bytes + 1)[:max_bytes]
        collected = {key: value for key, value in response.headers.items()}
        return HttpHop(status=status, headers=collected, body=body, location=location)
    finally:
        response.close()
