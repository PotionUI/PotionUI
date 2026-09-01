# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/pipelines/trellis2_image_to_3d.py
# (Trellis2ImageTo3DPipeline: preprocess_image, get_cond, sample_sparse_structure,
# sample_shape_slat, sample_shape_slat_cascade, decode_shape_slat, sample_tex_slat,
# decode_tex_slat, decode_latent, run).
"""The TRELLIS.2 image-to-mesh run: three flow stages over a shared voxel state.

The stages cannot be separate pipes — sparse structure hands shape a coordinate
set, shape hands texture both a latent and the octree subdivision decisions that
make the two decoders agree on a grid — so the whole cascade lives here as one
function over already-loaded models, and the pipe that calls it stays thin.

Three tiers, and they are not three grid sizes of one code path:

======  ====================================================================
tier    what runs
======  ====================================================================
512     one shape pass (the res-32 flow, conditioned at 512), decoded at 512
1024    cascade: res-32 LR pass -> octree upsample -> res-64 HR pass at 1024
1536    the same cascade targeting 1536, degrading toward 1024 on token budget
======  ====================================================================

Both cascades condition the LR pass at 512 and the HR pass at 1024, and both run
the same HR weights — the tier only changes the coordinate grid the HR pass is
quantised onto. The texture stage always runs at the tier's own conditioning.

Placement is this module's job: a stage moves its model to the compute device,
runs, and moves it back to CPU before the next one loads, because a 1536 run
holds more than one of these models' worth of VRAM otherwise. Callers keep the
modules cached on the CPU side across generations.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

import numpy as np
import torch

from ...sparse3d import SparseTensor
from .config import (
    SHAPE_SLAT_NORMALIZATION,
    STAGE_SAMPLING,
    TEX_SLAT_NORMALIZATION,
    SlatNormalization,
)
from .dual_grid import flexible_dual_grid_to_mesh
from .sampling import sample_flow_stage

__all__ = [
    "MeshVolume",
    "Trellis2Components",
    "TIERS",
    "denormalize_slat",
    "normalize_slat",
    "occupancy_to_coords",
    "prepare_image",
    "quantize_to_grid",
    "resolve_cascade_grid",
    "run_image_to_mesh",
]

#: The world-space box every TRELLIS.2 volume is defined in.
AABB: Tuple[Tuple[float, float, float], Tuple[float, float, float]] = (
    (-0.5, -0.5, -0.5),
    (0.5, 0.5, 0.5),
)

#: The coordinate grid a SLat flow runs on is the output resolution over this:
#: the decoders grow the octree through four transitions, 2x each.
_DECODER_GROWTH = 16

#: Octree levels ``upsample()`` grows the LR shape latent by before the HR pass
#: quantises it. Four levels = the same 16x the decoder applies.
_CASCADE_UPSAMPLE_TIMES = 4

#: The LR pass's own output resolution — the space ``upsample()`` leaves
#: coordinates in, and therefore what :func:`quantize_to_grid` normalises by.
_CASCADE_LR_RESOLUTION = 512

#: Below this the cascade stops degrading and accepts the token count: 1024 is
#: the HR weights' own tier, so there is nothing further to fall back to.
_CASCADE_RESOLUTION_FLOOR = 1024

#: What the degrade loop subtracts per attempt.
_CASCADE_RESOLUTION_STEP = 128

#: Alpha above this fraction of full opacity counts as subject when cropping.
_SUBJECT_ALPHA = 0.8

#: Longest edge the conditioning image is downscaled to before anything else.
_MAX_IMAGE_EDGE = 1024

#: tier -> (sparse-structure grid, shape-flow tier, texture-flow tier, cascade?)
TIERS = {
    "512": (32, "512", "512", False),
    "1024": (32, "1024", "1024", True),
    "1536": (32, "1024", "1024", True),
}


@dataclass
class Trellis2Components:
    """The eight models a run needs, all resident on CPU on arrival.

    ``shape_flow_hr`` is ``None`` for the single-pass 512 tier; every other
    field is required. ``matting`` is only consulted for an opaque image when
    background removal is requested.
    """

    conditioner: Any
    ss_flow: Any
    ss_vae: Any
    shape_flow_lr: Any
    shape_flow_hr: Any
    shape_decoder: Any
    tex_flow: Any
    tex_decoder: Any
    matting: Any = None


@dataclass
class MeshVolume:
    """A decoded mesh plus the PBR attribute volume it is textured from.

    ``coords``/``attrs`` are the texture decoder's active voxels — ``[L, 3]``
    integer coordinates on a ``resolution``-cubed grid and ``[L, 6]`` attributes
    laid out by ``postprocess.PBR_ATTR_LAYOUT``. ``voxel_size`` is that grid's,
    which after a token-budget degrade is not the tier the caller asked for.
    """

    vertices: torch.Tensor
    faces: torch.Tensor
    attrs: torch.Tensor
    coords: torch.Tensor
    resolution: int

    @property
    def voxel_size(self) -> float:
        return 1.0 / self.resolution


# -- image preparation ------------------------------------------------------


def prepare_image(image, matting=None):
    """Upstream's ``preprocess_image``: matte, crop to the subject, premultiply.

    An image that already carries a non-trivial alpha channel uses it directly
    and never touches ``matting``; an opaque one needs a matting model, and
    saying so is more useful than reconstructing the background along with the
    subject. Returns a square RGB image with the subject centred.
    """
    from PIL import Image

    has_alpha = False
    if image.mode == "RGBA":
        has_alpha = not bool(np.all(np.array(image)[:, :, 3] == 255))

    longest = max(image.size)
    if longest > _MAX_IMAGE_EDGE:
        scale = _MAX_IMAGE_EDGE / longest
        image = image.resize(
            (int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS
        )

    if has_alpha:
        matted = image
    else:
        if matting is None:
            raise ValueError(
                "background removal needs a matting model and none is loaded: the "
                "input image is fully opaque, so the subject cannot be separated "
                "from it. Select a BiRefNet checkpoint, supply an image that "
                "already has a transparent background, or turn background removal "
                "off."
            )
        matted = matting(image.convert("RGB"))

    pixels = np.array(matted)
    alpha = pixels[:, :, 3]
    subject = np.argwhere(alpha > _SUBJECT_ALPHA * 255)
    if subject.size == 0:
        raise ValueError(
            "background removal found no subject: no pixel is more than "
            f"{int(_SUBJECT_ALPHA * 100)}% opaque after matting."
        )

    left, top = int(subject[:, 1].min()), int(subject[:, 0].min())
    right, bottom = int(subject[:, 1].max()), int(subject[:, 0].max())
    centre_x, centre_y = (left + right) / 2, (top + bottom) / 2
    size = max(right - left, bottom - top)
    cropped = matted.crop((
        int(centre_x - size // 2), int(centre_y - size // 2),
        int(centre_x + size // 2), int(centre_y + size // 2),
    ))

    premultiplied = np.array(cropped).astype(np.float32) / 255.0
    premultiplied = premultiplied[:, :, :3] * premultiplied[:, :, 3:4]
    return Image.fromarray((premultiplied * 255).astype(np.uint8))


# -- cascade math -----------------------------------------------------------


def occupancy_to_coords(occupancy: torch.Tensor, resolution: int) -> torch.Tensor:
    """Active-voxel coordinates ``[N, 4]`` (batch column first) from the
    sparse-structure decoder's ``[B, 1, D, D, D]`` occupancy logits.

    The decoder always emits 64^3; a stage that wants a coarser grid max-pools
    down to it, so a voxel survives when any of its children was occupied.
    """
    occupied = occupancy > 0
    decoded_resolution = occupied.shape[2]
    if decoded_resolution != resolution:
        if decoded_resolution % resolution:
            raise ValueError(
                f"cannot pool a {decoded_resolution}^3 occupancy grid down to "
                f"{resolution}^3 — the resolutions are not an integer ratio"
            )
        ratio = decoded_resolution // resolution
        occupied = torch.nn.functional.max_pool3d(occupied.float(), ratio, ratio, 0) > 0.5
    return torch.argwhere(occupied)[:, [0, 2, 3, 4]].int()


def quantize_to_grid(coords: torch.Tensor, source_resolution: int, target_resolution: int) -> torch.Tensor:
    """``coords`` ``[N, 4]`` requantised from one voxel grid onto another.

    Cell centres (hence the ``+ 0.5``) are mapped into the unit cube and back
    out onto the target grid, then deduplicated: several source voxels collapse
    into one target voxel whenever the target is coarser, which is the whole
    point — it is what makes the token count fall as the cascade degrades.
    """
    target_grid = target_resolution // _DECODER_GROWTH
    quantised = torch.cat(
        [
            coords[:, :1],
            ((coords[:, 1:] + 0.5) / source_resolution * target_grid).int(),
        ],
        dim=1,
    )
    return quantised.unique(dim=0)


def resolve_cascade_grid(
    coords: torch.Tensor,
    target_resolution: int,
    max_num_tokens: int,
    source_resolution: int = _CASCADE_LR_RESOLUTION,
) -> Tuple[torch.Tensor, int]:
    """Quantise ``coords`` for the HR pass, degrading resolution to fit budget.

    The HR pass's cost is the token count, which is not known until the LR
    geometry is quantised. A shape that is too detailed for ``max_num_tokens``
    at the requested tier is decoded at a coarser one instead of failing, and
    the resolution actually used is returned — it is what the texture volume and
    the exported mesh are sized by.

    ``1024`` is the floor: it is the tier the HR weights are trained for, so
    there is nothing coarser to fall back to and the budget is exceeded instead.
    """
    resolution = target_resolution
    while True:
        quantised = quantize_to_grid(coords, source_resolution, resolution)
        if quantised.shape[0] < max_num_tokens or resolution <= _CASCADE_RESOLUTION_FLOOR:
            return quantised, resolution
        resolution -= _CASCADE_RESOLUTION_STEP


def normalize_slat(slat: SparseTensor, normalization: SlatNormalization) -> SparseTensor:
    """Per-channel standardisation, the direction a flow model expects."""
    mean, std = _normalization_tensors(normalization, slat)
    return slat.replace((slat.feats - mean) / std)


def denormalize_slat(slat: SparseTensor, normalization: SlatNormalization) -> SparseTensor:
    """Per-channel destandardisation, the direction a decoder expects."""
    mean, std = _normalization_tensors(normalization, slat)
    return slat.replace(slat.feats * std + mean)


def _normalization_tensors(
    normalization: SlatNormalization, slat: SparseTensor
) -> Tuple[torch.Tensor, torch.Tensor]:
    kwargs = {"device": slat.device, "dtype": slat.dtype}
    return (
        torch.tensor(normalization.mean, **kwargs).unsqueeze(0),
        torch.tensor(normalization.std, **kwargs).unsqueeze(0),
    )


# -- placement --------------------------------------------------------------


@contextlib.contextmanager
def _on_device(module, device):
    """Run a stage with ``module`` on ``device``, returning it to CPU after.

    Every model here is large enough that leaving a finished stage resident
    decides whether the next one fits, so this is unconditional rather than a
    low-VRAM mode.
    """
    module.to(device)
    try:
        yield module
    finally:
        module.to("cpu")
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _progress(callback, stage: str):
    if callback is None:
        return None
    return lambda step, total: callback(stage, step, total)


# -- the run ----------------------------------------------------------------


@torch.no_grad()
def run_image_to_mesh(
    components: Trellis2Components,
    image,
    *,
    tier: str = "1024",
    seed: int = 0,
    device: str | torch.device = "cuda",
    stage_settings: Optional[dict] = None,
    remove_background: bool = False,
    max_num_tokens: int = 49152,
    progress: Optional[Callable[[str, int, int], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> MeshVolume:
    """Reconstruct one image into a textured mesh volume.

    ``stage_settings`` overrides :data:`config.STAGE_SAMPLING` per stage key
    (``sparse_structure`` / ``shape`` / ``texture``). ``progress`` is called as
    ``(stage_name, step, total_steps)``.
    """
    if tier not in TIERS:
        raise ValueError(f"unknown resolution tier {tier!r}; expected one of {sorted(TIERS)}")
    ss_grid, _shape_tier, _tex_tier, is_cascade = TIERS[tier]
    if is_cascade and components.shape_flow_hr is None:
        raise ValueError(f"the {tier} tier is a cascade and needs a high-resolution shape flow")

    settings = {**STAGE_SAMPLING, **(stage_settings or {})}
    device = torch.device(device)
    generator = torch.Generator(device="cpu").manual_seed(int(seed))

    if remove_background:
        # BiRefNet runs at 1024x1024 and is unusably slow on CPU, so it gets the
        # same place-and-return treatment as every sampling stage — and is gone
        # again before the conditioner arrives.
        if components.matting is None:
            image = prepare_image(image, None)
        else:
            with _on_device(components.matting, device) as matting:
                image = prepare_image(image, matting)

    # Both conditioning sizes come from one encoder pass each, up front: the
    # cascade needs 512 for its LR stage and 1024 for everything after it.
    with _on_device(components.conditioner, device) as conditioner:
        cond_512 = conditioner.encode(image, 512)
        cond_high = conditioner.encode(image, 1024) if tier != "512" else None
    cond = cond_high if cond_high is not None else cond_512
    neg_512 = components.conditioner.negative(cond_512)
    neg = components.conditioner.negative(cond)

    coords = _sample_sparse_structure(
        components, cond_512, neg_512, ss_grid, settings, device, generator, progress, is_cancelled
    )
    if coords.shape[0] == 0:
        raise ValueError(
            "the sparse-structure stage produced an empty volume: nothing in the "
            "input image was reconstructed as occupied space."
        )

    shape_slat, resolution = _sample_shape(
        components, cond_512, neg_512, cond, neg, coords, tier, is_cascade,
        settings, device, generator, max_num_tokens, progress, is_cancelled,
    )

    tex_slat = _sample_texture(
        components, cond, neg, shape_slat, settings, device, generator, progress, is_cancelled
    )

    return _decode(components, shape_slat, tex_slat, resolution, device, progress)


def _sample_sparse_structure(
    components, cond, neg_cond, grid, settings, device, generator, progress, is_cancelled
) -> torch.Tensor:
    flow = components.ss_flow
    config = flow.config
    noise = torch.randn(
        cond.shape[0], config.in_channels, *([config.resolution] * 3), generator=generator
    ).to(device=device, dtype=cond.dtype)

    with _on_device(flow, device):
        latent = sample_flow_stage(
            flow, noise, cond, neg_cond, settings["sparse_structure"],
            on_step=_progress(progress, "sparse_structure"), is_cancelled=is_cancelled,
        )

    with _on_device(components.ss_vae, device) as decoder:
        occupancy = decoder(latent)
    return occupancy_to_coords(occupancy, grid)


def _sample_shape(
    components, cond_lr, neg_lr, cond, neg, coords, tier, is_cascade,
    settings, device, generator, max_num_tokens, progress, is_cancelled,
) -> Tuple[SparseTensor, int]:
    """The shape stage: one pass at 512, or LR -> upsample -> HR above it."""
    stage = settings["shape"]
    slat = _sample_slat(
        components.shape_flow_lr, coords, cond_lr, neg_lr, stage, device, generator,
        _progress(progress, "shape_lr" if is_cascade else "shape"), is_cancelled,
    )
    slat = denormalize_slat(slat, SHAPE_SLAT_NORMALIZATION)
    if not is_cascade:
        return slat, int(tier)

    with _on_device(components.shape_decoder, device) as decoder:
        upsampled = decoder.upsample(slat, upsample_times=_CASCADE_UPSAMPLE_TIMES)

    hr_coords, resolution = resolve_cascade_grid(upsampled, int(tier), max_num_tokens)
    slat = _sample_slat(
        components.shape_flow_hr, hr_coords, cond, neg, stage, device, generator,
        _progress(progress, "shape_hr"), is_cancelled,
    )
    return denormalize_slat(slat, SHAPE_SLAT_NORMALIZATION), resolution


def _sample_slat(
    flow, coords, cond, neg_cond, stage, device, generator, on_step, is_cancelled
) -> SparseTensor:
    noise = SparseTensor(
        feats=torch.randn(coords.shape[0], flow.in_channels, generator=generator).to(
            device=device, dtype=cond.dtype
        ),
        coords=coords.to(device),
    )
    with _on_device(flow, device):
        return sample_flow_stage(
            flow, noise, cond, neg_cond, stage, on_step=on_step, is_cancelled=is_cancelled
        )


def _sample_texture(
    components, cond, neg_cond, shape_slat, settings, device, generator, progress, is_cancelled
) -> SparseTensor:
    """The texture stage, conditioned on the shape latent it paints.

    The flow takes twice the channels it emits: the shape latent is concatenated
    onto the noise rather than cross-attended, so the texture lands on exactly
    the geometry the shape stage produced. It goes in normalised — the caller
    holds the denormalised copy the decoder needs.
    """
    flow = components.tex_flow
    normalized_shape = normalize_slat(shape_slat, SHAPE_SLAT_NORMALIZATION)
    noise_channels = flow.in_channels - normalized_shape.feats.shape[1]
    noise = normalized_shape.replace(
        torch.randn(normalized_shape.feats.shape[0], noise_channels, generator=generator).to(
            device=device, dtype=normalized_shape.dtype
        )
    )

    with _on_device(flow, device):
        slat = sample_flow_stage(
            flow, noise, cond, neg_cond, settings["texture"],
            forward_kwargs={"concat_cond": normalized_shape},
            on_step=_progress(progress, "texture"), is_cancelled=is_cancelled,
        )
    return denormalize_slat(slat, TEX_SLAT_NORMALIZATION)


def _decode(components, shape_slat, tex_slat, resolution, device, progress) -> MeshVolume:
    """Decode both latents onto one grid and convert the dual grid to a mesh.

    The texture decoder is handed the shape decoder's subdivision decisions
    (``guide_subs``) rather than predicting its own, which is what guarantees
    the attribute volume's coordinates address the mesh's own voxels.
    """
    if progress is not None:
        progress("decode", 0, 2)
    if int(shape_slat.coords[:, 0].max()) != 0:
        raise ValueError("a run reconstructs one image at a time; got a batched latent")

    with _on_device(components.shape_decoder, device) as decoder:
        decoder.set_resolution(resolution)
        shape = decoder(shape_slat, return_subs=True)

    with _on_device(components.tex_decoder, device) as decoder:
        texture = decoder(tex_slat, guide_subs=shape.subs) * 0.5 + 0.5

    if progress is not None:
        progress("decode", 1, 2)

    vertices, faces = flexible_dual_grid_to_mesh(
        coords=shape.coords[:, 1:],
        vertices=shape.vertices.feats.float(),
        intersected=shape.intersected.feats,
        quad_lerp=shape.quad_lerp.feats.float(),
        aabb=AABB,
        grid_size=resolution,
    )
    return MeshVolume(
        vertices=vertices,
        faces=faces,
        attrs=texture.feats.float(),
        coords=texture.coords[:, 1:],
        resolution=resolution,
    )
