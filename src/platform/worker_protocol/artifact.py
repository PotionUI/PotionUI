"""A checksummed reference to something a worker produced - never the bytes."""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field, JsonValue, field_validator

from src.platform.worker_protocol.common import (
    ContentDigest,
    Identifier,
    NonEmptyText,
    ProtocolModel,
    validate_contained_relative_path,
)


class ArtifactRefV1(ProtocolModel):
    """Where a produced artifact can be fetched, and what it should hash to.

    The bytes stay on the worker side until core (or a viewer) pulls them.
    Keeping the event stream free of payloads is what lets an event be small
    enough to replay, and lets a large video be fetched once rather than
    travelling through every hop of the stream.
    """


    artifact_id: Identifier
    #: Which output this is. The vocabulary is core's output-type registry,
    #: which plugins extend, so this is a free string rather than an enum.
    kind: Identifier
    media_type: NonEmptyText
    size_bytes: Annotated[int, Field(ge=0)]
    digest: ContentDigest
    #: Where the bytes live. Provider-scoped, possibly a signed URL with a
    #: lifetime shorter than the execution's - core must not treat it as
    #: durable storage.
    uri: NonEmptyText
    #: Suggested destination filename. Rejected at validation if it is absolute
    #: or escapes its root, so no consumer downstream has to remember to check.
    filename: Optional[str] = None
    #: Which pipe emitted it, when the worker knows.
    pipe_id: Optional[str] = None
    #: "gallery" for a final output nested in a worker's `GalleryGenerationOutput`,
    #: "preview" for a transient workbench preview, `None` for a bare leaf output
    #: (also what an older worker that predates this field always sends - core
    #: reads that the same way it reads an explicit `None`).
    role: Optional[Identifier] = None
    #: Carried from the nested `GenerationOutput` when it has one, so core can
    #: restore it without re-deriving it.
    seed: Optional[int] = None
    #: Carried from the nested `GenerationOutput`'s `derived` flag, when it has
    #: one (audio members don't).
    derived: Optional[bool] = None
    metadata: dict[str, JsonValue] = {}

    @field_validator("filename")
    @classmethod
    def _contained_filename(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_contained_relative_path(value)
