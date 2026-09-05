"""Shared conditioning-contract guard for the concat-only Wan i2v generators.

Both ``generator/img2vid_wan22`` (standalone i2v/flf) and
``generator/chain_video_wan22`` (Director i2v/flf/chain shots) implement ONLY
the 36-channel channel-CONCATENATED reference-frame conditioning of Wan 2.2's
``wan22_i2v_14b`` checkpoints -- prepending a constant concat tensor to the
noisy latent (see ``img2vid_wan22/concat.py``). Neither generator ever passes
``clip_fea`` into the DiT forward, so neither can serve a classic Wan 2.1 i2v
checkpoint (``wan_i2v_14b``, with or without the FLF ``emb_pos`` positional
table), which instead needs CLIP-vision image features injected through its
own ``img_emb`` cross-attention path (``arch/wan/model.py``'s ``MLPProj``,
built only when ``model_type == "i2v"`` -- see ``detect/unet_detect.py``).

Channel count alone can't tell the two apart: both ``wan_i2v_14b`` and
``wan22_i2v_14b`` report ``in_dim=36`` (``detect/registry.py``) -- only the
module's own ``img_emb`` attribute (populated exclusively for the
CLIP-vision variant, `None` on the concat-only one) distinguishes them.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional


def require_concat_i2v_contract_for_experts(
        experts: Iterable[Optional[Any]], *, generator: str, variant: str, mode_label: str,
) -> None:
    """Apply :func:`require_concat_i2v_contract` to every DiT wrapper in
    ``experts`` that isn't ``None`` (a single-expert bundle's absent
    low-noise slot). Call for every expert that can actually EXECUTE in a
    run, not just the high-noise expert routing decisions are made from --
    a compatible high-noise expert paired with an incompatible classic
    low-noise one must still reject, and which expert runs at a given sigma
    is a sampling-time decision this validation must not have to predict."""
    for dit in experts:
        if dit is None:
            continue
        require_concat_i2v_contract(dit.module, generator=generator, variant=variant, mode_label=mode_label)


def require_concat_i2v_contract(module: Any, *, generator: str, variant: str, mode_label: str) -> None:
    """Raise ``ValueError`` if ``module`` (a loaded ``WanModel``) needs
    CLIP-vision image features this concat-only conditioning path never
    supplies. Call before any VAE move/encode/offload, concat construction,
    or denoising -- for every expert that can actually execute (a dual-expert
    pair's low-noise expert included, not just the high-noise one used for
    routing decisions)."""
    img_emb = getattr(module, "img_emb", None)
    if img_emb is None:
        return
    flf = getattr(img_emb, "emb_pos", None) is not None
    raise ValueError(
        f"{generator}: loaded model '{variant}' is a classic Wan i2v checkpoint (CLIP-vision "
        f"conditioning via img_emb"
        + (", first-last-frame variant" if flf else "")
        + f"), which this generator's concat-only conditioning path does not support -- no Wan "
        f"generator supplies the clip_fea this checkpoint needs. {mode_label} mode needs a Wan "
        f"2.2 A14B concat-i2v checkpoint (in_dim=36, no CLIP-vision projector) instead."
    )
