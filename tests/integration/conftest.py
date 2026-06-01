"""Fixtures for integration tests.

``vcr_config`` configures pytest-recording (coding-conventions.md §8.4) for the
live-provider cassette: it strips the Anthropic API key and other auth/identifying
headers so no secret ever lands in a checked-in cassette. Record mode is left to
pytest-recording's default (``none`` — replay-only) so a missing cassette fails
loudly rather than silently hitting the network; re-record explicitly with
``--record-mode=once`` (with ANTHROPIC_API_KEY set) after deleting the stale file.
"""

from typing import Any

import pytest


@pytest.fixture
def vcr_config() -> dict[str, Any]:
    """VCR options applied to every cassette in this package.

    ``filter_headers`` strips the Anthropic API key (``x-api-key``) and other
    auth/identifying headers from both request and response before checkin.
    """
    return {
        "filter_headers": [
            "authorization",
            "x-api-key",
            "cookie",
            "set-cookie",
            "user-agent",
            "request-id",
            "x-request-id",
            "anthropic-organization-id",
        ],
        "decode_compressed_response": True,
    }
