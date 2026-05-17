"""``IngestionResult`` -- the Ingester's typed output.

Architectural source: ``implementation-plan.md`` §3.2.

Captured at the boundary between the (Phase-1) Ingester and downstream
stages: what URL was fetched, what its canonical form was after redirect
resolution, the SHA-256 of the normalized text, when the fetch happened,
how, the word count, the publisher domain, and where the cached payload
landed on disk. This is the metadata that pre-Extractor stages consume
and that the Extractor folds into the AttackSpec's source block.
"""

from datetime import datetime

from pydantic import Field

from cyberlab_gen.schemas.base import ArtifactModel
from cyberlab_gen.schemas.primitives import HttpUrl, NonEmptyString, Sha256Hex


class IngestionResult(ArtifactModel):
    """Typed output of the Ingester stage. ``implementation-plan.md`` §3.2."""

    url: HttpUrl
    canonical_url: HttpUrl
    content_hash: Sha256Hex
    fetched_at: datetime
    fetch_method: NonEmptyString
    word_count: int = Field(ge=0)
    publisher_domain: NonEmptyString
    cached_path: NonEmptyString


__all__ = ["IngestionResult"]
