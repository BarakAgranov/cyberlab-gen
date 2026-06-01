"""Fixtures for integration tests.

``vcr_config`` configures pytest-recording (coding-conventions.md §8.4) for the
live-provider cassette so no secret or identifying header ever lands in a
checked-in cassette. Record mode is left to pytest-recording's default
(``none`` — replay-only) so a missing cassette fails loudly rather than silently
hitting the network; re-record explicitly with ``--record-mode=once`` (with
ANTHROPIC_API_KEY set) after deleting the stale file.

Header scrubbing is two-sided on purpose: vcrpy's ``filter_headers`` only applies
to *request* headers, so it strips the Anthropic auth (``x-api-key`` /
``authorization``) and ``user-agent`` from the request, but leaves *response*
headers untouched. Anthropic responses carry identifying/session headers
(``anthropic-organization-id``, ``request-id``, Cloudflare ``set-cookie``); a
``before_record_response`` hook scrubs those explicitly (case-insensitively)
before the cassette is written.
"""

from typing import Any, cast

import pytest

#: Headers removed from both sides of a recorded interaction. Anthropic auth
#: plus identifying/session headers. ``filter_headers`` covers the request;
#: ``_scrub_response_headers`` covers the response (vcrpy does not apply
#: ``filter_headers`` to responses).
_SENSITIVE_HEADERS = (
    "authorization",
    "x-api-key",
    "cookie",
    "set-cookie",
    "user-agent",
    "request-id",
    "x-request-id",
    "anthropic-organization-id",
)


def _scrub_response_headers(response: dict[str, Any]) -> dict[str, Any]:
    """Drop sensitive/identifying headers from a recorded response (case-insensitive)."""
    headers_obj = response.get("headers")
    if isinstance(headers_obj, dict):
        headers = cast("dict[str, Any]", headers_obj)
        drop = {name.lower() for name in _SENSITIVE_HEADERS}
        for key in [key for key in headers if key.lower() in drop]:
            del headers[key]
    return response


@pytest.fixture
def vcr_config() -> dict[str, Any]:
    """VCR options applied to every cassette in this package.

    ``filter_headers`` strips auth/identifying headers from the *request*;
    ``before_record_response`` strips the same set from the *response* before
    check-in (vcrpy's ``filter_headers`` is request-only).
    """
    return {
        "filter_headers": list(_SENSITIVE_HEADERS),
        "before_record_response": _scrub_response_headers,
        "decode_compressed_response": True,
    }
