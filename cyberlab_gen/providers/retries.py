"""Retry-strategy parameters for the provider layer.

Phase 0 ships strategy data only — the actual ``async def with_retries``
loop driver lands in Phase 1 alongside the Anthropic adapter body. The
mock provider does not retry; the Anthropic scaffold raises
``NotImplementedError("Phase 1")``. Recorded explicitly so a future
reader does not expect executable retry logic here yet.

Architectural source: ``provider-interface.md`` §6.1 (transient) and
§6.2 (malformed output).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryStrategy:
    """Backoff parameters for a class of provider failures.

    Per ``provider-interface.md`` §6.1: transient failures get 3 attempts
    (initial + 2 retries), base delay 1s, exponential factor 2, jitter ±30%.
    The malformed-output strategy (§6.2) uses a *lower* attempt count (2:
    initial + 1 retry) to cap the worst-case call count, and the model is
    re-prompted with the previous parse error rather than backing off in
    time, so the delay/jitter values are unused by that path — they remain
    here for symmetry and to keep the §6 retry surface a single data shape.
    """

    max_attempts: int
    base_delay_seconds: float
    backoff_factor: float
    jitter_fraction: float


TRANSIENT_RETRIES: RetryStrategy = RetryStrategy(
    max_attempts=3,
    base_delay_seconds=1.0,
    backoff_factor=2.0,
    jitter_fraction=0.3,
)


# Default lowered from 3 to 2 (initial + 1 retry) per ADR 0018 / provider-interface.md
# §6.2 to cap the worst-case provider call count for a persistently malformed stream.
MALFORMED_OUTPUT_RETRIES: RetryStrategy = RetryStrategy(
    max_attempts=2,
    base_delay_seconds=0.0,
    backoff_factor=1.0,
    jitter_fraction=0.0,
)
