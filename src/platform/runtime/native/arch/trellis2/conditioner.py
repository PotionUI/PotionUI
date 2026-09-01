# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/modules/conditioner.py
# (DinoV3FeatureExtractor.extract_features / its preprocessing transform).
"""DINOv3 ViT-L/16 image conditioner: the only conditioning TRELLIS.2 takes.

Every flow stage cross-attends to a ``(B, N, 1024)`` feature sequence produced
here, and the negative branch is a zero tensor of the same shape — there is no
text encoder anywhere in this family.

Upstream constructs the encoder with ``DINOv3ViTModel.from_pretrained`` against
a **gated** HuggingFace repo, which downloads and requires accepted terms. This
module never reaches the hub: the model is built from the inlined
``DinoV3Config`` and filled from a single local safetensors file, whose blocks
sit at ``layer.N.*`` where ``transformers`` nests them one level deeper at
``model.layer.N.*``.

Upstream's feature vector is also not the model's own ``forward`` output: it
runs embeddings -> rope -> blocks and then a *parameterless* ``layer_norm``,
skipping the trained final ``norm``. ``encode`` reproduces that step for step.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Mapping, Sequence

import torch
import torch.nn.functional as F

from .config import DINO_V3_VIT_L16, DinoV3Config

if TYPE_CHECKING:  # pragma: no cover - typing only
    from PIL.Image import Image as PILImage

__all__ = [
    "DinoV3ImageConditioner",
    "IMAGENET_MEAN",
    "IMAGENET_STD",
    "build_dino_v3",
    "load_dino_v3",
    "remap_dino_checkpoint",
]

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)

_CHECKPOINT_BLOCK_PREFIX = "layer."
_MODULE_BLOCK_PREFIX = "model.layer."


def remap_dino_checkpoint(state: Mapping[str, Any]) -> Dict[str, Any]:
    """Move the checkpoint's ``layer.N.*`` blocks under ``model.layer.N.*``.

    ``embeddings.*`` and ``norm.*`` already line up, so only the block keys move.
    """
    return {
        (
            _MODULE_BLOCK_PREFIX + key[len(_CHECKPOINT_BLOCK_PREFIX):]
            if key.startswith(_CHECKPOINT_BLOCK_PREFIX)
            else key
        ): value
        for key, value in state.items()
    }


def build_dino_v3(config: DinoV3Config = DINO_V3_VIT_L16):
    """A randomly-initialised ``DINOv3ViTModel`` from inlined values only.

    No repo id, no cache lookup, no network — ``transformers`` is used purely as
    the architecture, and the weights arrive from a local file.
    """
    try:
        from transformers import DINOv3ViTConfig, DINOv3ViTModel
    except ImportError as exc:
        raise RuntimeError(
            "The installed transformers version does not ship DINOv3 "
            "(transformers.DINOv3ViTConfig / DINOv3ViTModel). TRELLIS.2's image "
            "conditioner needs it; upgrade transformers."
        ) from exc

    return DINOv3ViTModel(DINOv3ViTConfig(**config.as_kwargs()))


def _transformer_blocks(model):
    """The block list, wherever this ``transformers`` version keeps it."""
    inner = getattr(model, "model", None)
    blocks = getattr(inner, "layer", None) if inner is not None else None
    if blocks is None:
        blocks = getattr(model, "layer", None)
    if blocks is None:
        raise RuntimeError(
            "The installed transformers version lays DINOv3 out differently than "
            "this module expects: neither model.model.layer nor model.layer exists."
        )
    return blocks


def load_dino_v3(
    path: str | Path,
    config: DinoV3Config = DINO_V3_VIT_L16,
    *,
    dtype: torch.dtype | None = None,
    device: torch.device | str | None = None,
):
    """Build the encoder and fill it from a single local safetensors file.

    ``strict=True`` is not usable — the checkpoint legitimately omits
    non-persistent buffers — so every *parameter* is checked for a fill instead.
    A prefix that matches nothing would otherwise leave a randomly-initialised
    encoder reporting success, and surface as bad geometry much later.
    """
    from safetensors.torch import load_file

    model = build_dino_v3(config)
    state = remap_dino_checkpoint(load_file(str(path)))
    result = model.load_state_dict(state, strict=False)

    unfilled = sorted({name for name, _ in model.named_parameters()}.intersection(result.missing_keys))
    if unfilled:
        raise ValueError(
            f"{Path(path).name} is not a DINOv3 ViT-L/16 image encoder: "
            f"{len(unfilled)} weights were left unfilled, first few {unfilled[:5]}."
        )

    model.eval()
    if dtype is not None or device is not None:
        model.to(device=device, dtype=dtype)
    return model


class DinoV3ImageConditioner:
    """Turns an image into the conditioning sequence every TRELLIS.2 stage reads.

    Deliberately not an ``nn.Module``: the wrapped ``DINOv3ViTModel`` is the
    thing loaders fill and parity tests compare, and nesting it would prefix
    every key. ``to``/``cuda``/``cpu`` delegate so it still moves like one.
    """

    def __init__(self, model, config: DinoV3Config = DINO_V3_VIT_L16) -> None:
        self.model = model
        self.config = config

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        config: DinoV3Config = DINO_V3_VIT_L16,
        *,
        dtype: torch.dtype | None = None,
        device: torch.device | str | None = None,
    ) -> "DinoV3ImageConditioner":
        return cls(load_dino_v3(path, config, dtype=dtype, device=device), config)

    # -- placement ----------------------------------------------------------

    @property
    def device(self) -> torch.device:
        return self.model.embeddings.patch_embeddings.weight.device

    @property
    def dtype(self) -> torch.dtype:
        return self.model.embeddings.patch_embeddings.weight.dtype

    def to(self, *args, **kwargs) -> "DinoV3ImageConditioner":
        self.model.to(*args, **kwargs)
        return self

    def cuda(self) -> "DinoV3ImageConditioner":
        return self.to("cuda")

    def cpu(self) -> "DinoV3ImageConditioner":
        return self.to("cpu")

    # -- preprocessing ------------------------------------------------------

    @staticmethod
    def preprocess(images: "PILImage | Sequence[PILImage]", size: int) -> torch.Tensor:
        """PIL image(s) -> a normalised ``(B, 3, size, size)`` float tensor.

        LANCZOS to a square, RGB, /255, then ImageNet mean/std — upstream's
        transform, and the encoder is sensitive to all of it.
        """
        import numpy as np
        from PIL import Image

        if not isinstance(images, (list, tuple)):
            images = [images]

        batch = torch.stack([
            torch.from_numpy(
                np.asarray(
                    image.resize((size, size), Image.LANCZOS).convert("RGB"),
                    dtype=np.float32,
                )
                / 255.0
            ).permute(2, 0, 1)
            for image in images
        ])

        mean = torch.tensor(IMAGENET_MEAN).view(1, 3, 1, 1)
        std = torch.tensor(IMAGENET_STD).view(1, 3, 1, 1)
        return (batch - mean) / std

    # -- encoding -----------------------------------------------------------

    def encode(
        self,
        image: "torch.Tensor | PILImage | Sequence[PILImage]",
        size: int = 512,
    ) -> torch.Tensor:
        """``(B, num_tokens(size), hidden_size)`` conditioning features.

        A ``(B, 3, H, W)`` tensor is taken as already preprocessed and used as
        given; anything else goes through :meth:`preprocess` at ``size`` first.
        """
        if isinstance(image, torch.Tensor):
            if image.ndim != 4:
                raise ValueError(
                    f"image tensor must be batched (B, C, H, W), got shape {tuple(image.shape)}"
                )
            pixels = image
        else:
            pixels = self.preprocess(image, size)

        pixels = pixels.to(device=self.device, dtype=self.dtype)

        with torch.no_grad():
            hidden = self.model.embeddings(pixels, bool_masked_pos=None)
            position_embeddings = self.model.rope_embeddings(pixels)
            for block in _transformer_blocks(self.model):
                hidden = block(hidden, position_embeddings=position_embeddings)
            return F.layer_norm(hidden, hidden.shape[-1:])

    @staticmethod
    def negative(cond: torch.Tensor) -> torch.Tensor:
        """The unconditional branch: zeros shaped like the conditional one."""
        return torch.zeros_like(cond)
