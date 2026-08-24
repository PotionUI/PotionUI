"""The content-addressed set of user-media files a package's pipes reference.

A pipe config produced by the assembly step can carry a real path into the
core host's storage directory - a path a remote worker has no filesystem
access to. Collection (``src.features.generation.input_assets``) walks those
configs, replaces every such path with an ``asset://<logical_id>`` token, and
returns the manifest below alongside the rewritten pipes. Mirrors
``model_bundle.py``'s shape and validation approach - see that module for the
established pattern.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field, field_validator, model_validator

from src.platform.worker_protocol.common import (
    ContentDigest,
    Identifier,
    NonEmptyText,
    ProtocolModel,
    validate_contained_relative_path,
)


class InputAssetV1(ProtocolModel):
    """One user-media file a processed pipeline references by token."""

    #: The id a pipe config's ``asset://<logical_id>`` token refers to.
    #: Derived from the file's content digest, so the same file submitted
    #: through two different fields dedups to one entry.
    logical_id: Identifier
    #: Best-effort MIME/media kind (``image``, ``video``, ...); absent when
    #: the collector had no cheap way to know it. Advisory only - a worker
    #: does not need it to stage the file.
    media_type: Optional[str] = None
    #: Where the file lands inside the worker's staging area. Relative and
    #: contained - see validate_contained_relative_path.
    relative_path: NonEmptyText
    digest: ContentDigest
    size_bytes: Annotated[int, Field(gt=0)]

    @field_validator("relative_path")
    @classmethod
    def _contained(cls, value: str) -> str:
        return validate_contained_relative_path(value)


class InputAssetManifestV1(ProtocolModel):
    """The complete set of input media one execution package carries."""


    assets: tuple[InputAssetV1, ...] = ()
    #: Derived from ``assets`` when absent; cross-checked against them when
    #: present, so a hand-written or re-encoded manifest cannot disagree with
    #: itself about how many bytes a worker is about to stage.
    total_size_bytes: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="before")
    @classmethod
    def _derive_total(cls, data: object) -> object:
        if isinstance(data, dict) and "total_size_bytes" not in data:
            assets = data.get("assets") or ()
            try:
                data = {
                    **data,
                    "total_size_bytes": sum(
                        a["size_bytes"] if isinstance(a, dict) else a.size_bytes
                        for a in assets
                    ),
                }
            except (TypeError, KeyError, AttributeError):
                return data
        return data

    @model_validator(mode="after")
    def _consistent(self) -> "InputAssetManifestV1":
        seen_logical: set[str] = set()
        seen_paths: list[tuple[str, ...]] = []
        for asset in self.assets:
            if asset.logical_id in seen_logical:
                raise ValueError(f"duplicate logical_id {asset.logical_id!r}")
            seen_logical.add(asset.logical_id)

            parts = tuple(p for p in asset.relative_path.replace("\\", "/").split("/") if p)
            for other in seen_paths:
                if _overlaps(parts, other):
                    raise ValueError(
                        f"relative_path {asset.relative_path!r} overlaps another entry"
                    )
            seen_paths.append(parts)

        expected = sum(asset.size_bytes for asset in self.assets)
        if self.total_size_bytes != expected:
            raise ValueError(
                f"total_size_bytes {self.total_size_bytes} does not match the "
                f"sum of asset sizes {expected}"
            )
        return self

    def asset(self, logical_id: str) -> InputAssetV1 | None:
        for candidate in self.assets:
            if candidate.logical_id == logical_id:
                return candidate
        return None


def _overlaps(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """True if one path is the other, or an ancestor directory of the other."""
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return longer[: len(shorter)] == shorter
