"""Tests for ``IngestionResult``.

Architectural source: ``implementation-plan.md`` §3.2.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from cyberlab_gen.schemas import IngestionResult


def _payload(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401 — heterogeneous Pydantic payload overrides
    # Test-only helper: kwargs are forwarded into a Pydantic payload dict
    # whose values are intentionally heterogeneous (str, int, datetime), so
    # `Any` is the right type for the overrides bag.
    base: dict[str, Any] = {
        "url": "https://example.com/blog/post",
        "canonical_url": "https://example.com/blog/post",
        "content_hash": "a" * 64,
        "fetched_at": datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC),
        "fetch_method": "http_get",
        "word_count": 1234,
        "publisher_domain": "example.com",
        "cached_path": "cache/abcdef/post.html",
    }
    base.update(overrides)
    return base


def test_ingestion_result_happy_path() -> None:
    result = IngestionResult.model_validate(_payload())
    assert result.word_count == 1234
    assert result.publisher_domain == "example.com"


@pytest.mark.parametrize(
    "missing_field",
    [
        "url",
        "canonical_url",
        "content_hash",
        "fetched_at",
        "fetch_method",
        "word_count",
        "publisher_domain",
        "cached_path",
    ],
)
def test_ingestion_result_all_fields_required(missing_field: str) -> None:
    payload = _payload()
    del payload[missing_field]
    with pytest.raises(ValidationError) as exc:
        IngestionResult.model_validate(payload)
    assert missing_field in str(exc.value)


def test_ingestion_result_rejects_non_url_in_url() -> None:
    with pytest.raises(ValidationError):
        IngestionResult.model_validate(_payload(url="not a url"))


def test_ingestion_result_rejects_non_url_in_canonical_url() -> None:
    with pytest.raises(ValidationError):
        IngestionResult.model_validate(_payload(canonical_url="not a url"))


def test_ingestion_result_rejects_non_hex_content_hash() -> None:
    with pytest.raises(ValidationError):
        IngestionResult.model_validate(_payload(content_hash="not-hex" * 10))


def test_ingestion_result_rejects_wrong_length_content_hash() -> None:
    with pytest.raises(ValidationError):
        IngestionResult.model_validate(_payload(content_hash="a" * 32))


def test_ingestion_result_rejects_negative_word_count() -> None:
    with pytest.raises(ValidationError):
        IngestionResult.model_validate(_payload(word_count=-1))


def test_ingestion_result_rejects_empty_fetch_method() -> None:
    with pytest.raises(ValidationError):
        IngestionResult.model_validate(_payload(fetch_method=""))


def test_ingestion_result_rejects_empty_publisher_domain() -> None:
    with pytest.raises(ValidationError):
        IngestionResult.model_validate(_payload(publisher_domain=""))


def test_ingestion_result_rejects_empty_cached_path() -> None:
    with pytest.raises(ValidationError):
        IngestionResult.model_validate(_payload(cached_path=""))


def test_ingestion_result_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError, match="bogus"):
        IngestionResult.model_validate(_payload(bogus="nope"))


def test_ingestion_result_round_trips_through_model_dump() -> None:
    original = IngestionResult.model_validate(_payload())
    restored = IngestionResult.model_validate(original.model_dump())
    assert restored == original
