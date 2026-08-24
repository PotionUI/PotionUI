"""The content-addressed set of model files a package needs on disk."""

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


class ModelBundleEntryV1(ProtocolModel):
    """One file the worker must have staged before the package can run."""

    #: The id the execution package refers to this file by. Stable across
    #: providers and independent of where the bytes came from.
    logical_id: Identifier
    #: Destination role in the worker's model depot (``checkpoint``, ``lora``,
    #: ``vae``, ``text_encoder``, ...). Deliberately a free string: model types
    #: are extended by plugins, so a closed enum here would make adding a model
    #: type a protocol version bump.
    role: Identifier
    #: Where the file lands inside its role's directory. Relative and
    #: contained - see validate_contained_relative_path.
    relative_path: NonEmptyText
    digest: ContentDigest
    size_bytes: Annotated[int, Field(ge=0)]
    #: Where the worker may fetch the bytes. Provider-scoped and possibly
    #: signed; opaque to core beyond being a string.
    source_uri: Optional[str] = None

    @field_validator("relative_path")
    @classmethod
    def _contained(cls, value: str) -> str:
        return validate_contained_relative_path(value)


class ModelBundleManifestV1(ProtocolModel):
    """The complete model working set for one execution, content-addressed."""


    bundle_id: Identifier
    #: Content address of the bundle as a whole. Lets a worker recognise a
    #: bundle it has already staged without walking every entry.
    bundle_digest: ContentDigest
    entries: tuple[ModelBundleEntryV1, ...] = ()
    #: Derived from ``entries`` when absent; cross-checked against them when
    #: present, so a hand-written or re-encoded manifest cannot disagree with
    #: itself about how many bytes a worker is about to pull.
    total_size_bytes: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="before")
    @classmethod
    def _derive_total(cls, data: object) -> object:
        if isinstance(data, dict) and "total_size_bytes" not in data:
            entries = data.get("entries") or ()
            try:
                data = {
                    **data,
                    "total_size_bytes": sum(
                        e["size_bytes"] if isinstance(e, dict) else e.size_bytes
                        for e in entries
                    ),
                }
            except (TypeError, KeyError, AttributeError):
                return data
        return data

    @model_validator(mode="after")
    def _consistent(self) -> "ModelBundleManifestV1":
        seen_logical: set[str] = set()
        seen_destination: set[tuple[str, str]] = set()
        for entry in self.entries:
            if entry.logical_id in seen_logical:
                raise ValueError(f"duplicate logical_id {entry.logical_id!r}")
            seen_logical.add(entry.logical_id)

            destination = (entry.role, entry.relative_path)
            if destination in seen_destination:
                raise ValueError(
                    f"two entries write the same destination "
                    f"{entry.role}/{entry.relative_path}"
                )
            seen_destination.add(destination)

        expected = sum(entry.size_bytes for entry in self.entries)
        if self.total_size_bytes != expected:
            raise ValueError(
                f"total_size_bytes {self.total_size_bytes} does not match the "
                f"sum of entry sizes {expected}"
            )
        return self

    def entry(self, logical_id: str) -> ModelBundleEntryV1 | None:
        for candidate in self.entries:
            if candidate.logical_id == logical_id:
                return candidate
        return None
