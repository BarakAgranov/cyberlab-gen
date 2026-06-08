"""Retry-strategy parameters for the provider layer.

``TRANSIENT_RETRIES`` is the backoff strategy the Ingestion fetcher uses for transient
HTTP failures (``framework/ingestion.py``). The provider's *malformed-output* retry now
lives in the pydantic-ai layer (``AnthropicProvider``'s ``retries={'output': N}``, ADR
0036), so the former ``MALFORMED_OUTPUT_RETRIES`` strategy here became dead and was removed.

Architectural source: ``provider-interface.md`` §6.1 (transient).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryStrategy:
    """Backoff parameters for a class of provider failures.

    Per ``provider-interface.md`` §6.1: transient failures get 3 attempts (initial + 2
    retries), base delay 1s, exponential factor 2, jitter ±30% (``TRANSIENT_RETRIES``).
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
