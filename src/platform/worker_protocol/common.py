"""The shared model base and the value types more than one payload uses."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


class ProtocolModel(BaseModel):
    """Base for every worker-protocol payload and every part of one.

    Unknown fields are rejected rather than dropped: a worker that silently
    discarded a new, meaningful field of an execution package would run the
    wrong job. Adding a field is therefore a schema-version bump, which is what
    ``envelope.py`` exists to make legible.

    Carries no version of its own - the envelope holds the version, exactly
    once, for the whole document.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

#: Digest algorithms core will verify. Restricted rather than free-form: a
#: worker that declares an algorithm core cannot compute cannot be checked, and
#: an unverifiable digest is worse than an absent one because it reads as proof.
#:
#: Deliberately one element. Anything listed here must be computable by this
#: build - an algorithm accepted at validation but unavailable at verification
#: moves the failure to the worst possible place, after the document has been
#: accepted. Widen it the day a real peer needs another, together with the
#: dependency that makes it computable.
DIGEST_ALGORITHMS = ("sha256",)

_HEX_RE = re.compile(r"\A[0-9a-f]+\Z")

Identifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]

NonEmptyText = Annotated[str, StringConstraints(min_length=1)]


class ContentDigest(ProtocolModel):
    """A content address: the algorithm plus the lowercase hex digest."""

    algorithm: Annotated[str, Field(description="One of DIGEST_ALGORITHMS.")]
    hex: NonEmptyText

    @field_validator("algorithm")
    @classmethod
    def _known_algorithm(cls, value: str) -> str:
        if value not in DIGEST_ALGORITHMS:
            raise ValueError(
                f"unsupported digest algorithm {value!r}; "
                f"expected one of {', '.join(DIGEST_ALGORITHMS)}"
            )
        return value

    @field_validator("hex")
    @classmethod
    def _lowercase_hex(cls, value: str) -> str:
        if not _HEX_RE.fullmatch(value):
            raise ValueError("digest must be lowercase hexadecimal")
        return value

    def __str__(self) -> str:
        return f"{self.algorithm}:{self.hex}"


def validate_contained_relative_path(value: str) -> str:
    """Reject a path that is absolute or escapes its destination root.

    A bundle entry names where a worker writes a downloaded file. Without this
    check a manifest could place a file anywhere the worker process can write
    by using ``..`` or a leading ``/``, which turns "fetch these weights" into
    arbitrary file placement.
    """
    if not value:
        raise ValueError("path must not be empty")
    if value.startswith("/") or value.startswith("\\"):
        raise ValueError(f"path must be relative, got {value!r}")
    if re.match(r"\A[A-Za-z]:", value):
        raise ValueError(f"path must be relative, got {value!r}")

    parts = [p for p in re.split(r"[\\/]+", value) if p not in ("", ".")]
    if not parts:
        raise ValueError(f"path resolves to nothing: {value!r}")

    depth = 0
    for part in parts:
        if part == "..":
            depth -= 1
            if depth < 0:
                raise ValueError(f"path escapes its root: {value!r}")
        else:
            depth += 1
    return value
