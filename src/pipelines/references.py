from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import torch

REFERENCE_KINDS = ("image", "video", "audio")


@dataclass(frozen=True, eq=False)
class PreEncodedReference:
    kind: Literal["image", "video", "audio"]
    latent: torch.Tensor
    name: str
    fingerprint: str

    def __post_init__(self) -> None:
        import torch

        if self.kind not in REFERENCE_KINDS:
            raise ValueError(
                f"PreEncodedReference {self.name!r}: kind must be one of {REFERENCE_KINDS}, got {self.kind!r}"
            )
        if not isinstance(self.latent, torch.Tensor):
            raise TypeError(
                f"PreEncodedReference {self.name!r}: latent must be a torch.Tensor, got {type(self.latent).__name__}"
            )
        if not self.fingerprint:
            raise ValueError(f"PreEncodedReference {self.name!r}: fingerprint must be a non-empty string")


def pre_encoded_references(value: Any) -> list[PreEncodedReference]:
    if value is None:
        return []
    if isinstance(value, PreEncodedReference):
        return [value]
    if isinstance(value, (list, tuple)):
        return [reference for item in value for reference in pre_encoded_references(item)]
    raise TypeError(f"'reference_latents' expects PreEncodedReference values, got {type(value).__name__}")
