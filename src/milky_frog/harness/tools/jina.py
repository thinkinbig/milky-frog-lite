from __future__ import annotations

from milky_frog.harness.tools._http import REDIRECT_STATUSES, guarded_hop

# Mirrors fetch.py's _MAX_DOWNLOAD_BYTES: bounds how much of a Jina response we
# pull into memory, independent of any downstream truncation.
MAX_RESPONSE_BYTES = 10 * 1024 * 1024

_USER_AGENT = "milky-frog-jina/1.0"


class JinaRedirectError(Exception):
    """Jina responded with a redirect instead of content.

    Jina is a trusted third party, but a redirect Location is attacker- or
    outage-influenceable. Auto-following it would run the target through
    urllib with none of fetch.py's SSRF/host guards, so callers must not
    follow it; they see this error instead and can fall back gracefully.
    """


def jina_request(
    url: str,
    api_key: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: float,
) -> tuple[int, bytes]:
    """Call a Jina endpoint with bearer auth, a bounded read, and no auto-redirect.

    Returns ``(status, raw_body)``, capped at ``MAX_RESPONSE_BYTES`` by the
    shared ``guarded_hop``. Raises ``JinaRedirectError`` rather than following a
    redirect. A 4xx/5xx comes back as its status for the caller to judge. Runs
    synchronously; callers dispatch it to a worker thread.
    """
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": _USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)
    hop = guarded_hop(
        url,
        method=method,
        data=data,
        headers=headers,
        timeout=timeout,
        max_bytes=MAX_RESPONSE_BYTES,
    )
    if hop.status in REDIRECT_STATUSES:
        raise JinaRedirectError(f"Jina responded with an unexpected redirect ({hop.status})")
    return hop.status, hop.body
