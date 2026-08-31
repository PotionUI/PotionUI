"""A core -> worker instruction: pull one model file straight from a URL into
the depot, verified the same way a staged upload is verified.

Distinct from ``ModelBundleEntryV1.source_uri`` (which core dereferences
itself, then streams the bytes to the worker's staging endpoint): this lets
core hand the worker a presigned/CDN URL and have the worker pull directly,
without proxying multi-gigabyte bytes through core.
"""

from __future__ import annotations

from typing import Annotated, Mapping, Optional

from pydantic import Field, field_validator

from src.platform.worker_protocol.common import (
    ContentDigest,
    NonEmptyText,
    ProtocolModel,
    validate_contained_relative_path,
)


class ModelFetchRequestV1(ProtocolModel):
    """Body of ``POST /v1/models/fetch``."""

    relative_path: NonEmptyText
    expected_digest: ContentDigest
    expected_size: Annotated[int, Field(ge=0)]
    #: Opaque to the worker beyond being a URL it streams a GET from -
    #: possibly presigned, possibly redirecting once (a CDN in front of the
    #: real object store).
    url: NonEmptyText
    headers: Optional[Mapping[str, str]] = None

    @field_validator("relative_path")
    @classmethod
    def _contained(cls, value: str) -> str:
        return validate_contained_relative_path(value)
