"""Fixed prompt-embedding loader for SeedVR2.

SeedVR2 is a restoration/upscale model that does NOT run a live text encoder:
it conditions on two *precomputed* text embeddings — a generic positive and a
generic negative — shipped as raw ``torch.save``d tensors (``models/clip/
seedvr2_pos_emb.pt`` / ``seedvr2_neg_emb.pt``). Each is a ``(sequence, 5120)``
tensor in the model's text-encoder width (``txt_in_dim``), fed straight into the
DiT's ``txt_in`` projection as the cross-modal ``txt`` stream. The model loader
pipe (a sibling task) loads these once and hands them to the generator; there is
no tokenizer, prompt string, or CFG negative-prompt path.
"""

from __future__ import annotations

from pathlib import Path

import torch


def load_seedvr2_prompt_embedding(path: str | Path) -> torch.Tensor:
    """Load a fixed SeedVR2 prompt embedding from a ``torch.save``d ``.pt`` file.

    Returns the raw ``(sequence, 5120)`` embedding tensor (``txt_in_dim`` width).
    Uses ``weights_only=True`` (the files are plain tensors, no pickled code) and
    maps to CPU; the caller moves/casts it alongside the DiT.
    """
    obj = torch.load(Path(path), weights_only=True, map_location="cpu")
    if not isinstance(obj, torch.Tensor):
        raise TypeError(
            f"SeedVR2 prompt embedding '{Path(path).name}' must be a raw tensor, "
            f"got {type(obj).__name__}"
        )
    if obj.ndim != 2:
        raise ValueError(
            f"SeedVR2 prompt embedding '{Path(path).name}' must be 2D (sequence, dim), "
            f"got shape {tuple(obj.shape)}"
        )
    return obj
