"""Fixtures for the framework unit tests.

``vcr_config`` configures pytest-recording (coding-conventions.md §8.4):
secrets are filtered out of cassettes before checkin. The record mode is left
to pytest-recording's default (``none`` — replay-only), so a missing cassette
fails loudly rather than silently hitting the network; re-record explicitly
with ``--record-mode=once`` after deleting the stale cassette.
"""

from typing import Any

import pytest


@pytest.fixture
def vcr_config() -> dict[str, Any]:
    """VCR options applied to every cassette in this package.

    ``filter_headers`` strips request auth and identifying headers so no secret
    or fingerprint ever lands in a checked-in cassette (coding-conventions.md
    §8.4). Record mode is intentionally not pinned here so the ``--record-mode``
    CLI flag governs it.
    """
    return {
        "filter_headers": ["authorization", "cookie", "set-cookie", "user-agent"],
    }
