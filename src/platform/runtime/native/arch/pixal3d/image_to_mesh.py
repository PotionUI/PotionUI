from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import torch

from ..trellis2.config import SHAPE_SLAT_NORMALIZATION, STAGE_SAMPLING
from ..trellis2.image_to_mesh import (
    MeshVolume,
    Trellis2Components,
    _decode,
    _on_device,
    _progress,
    _sample_slat,
    _sample_texture,
    denormalize_slat,
    occupancy_to_coords,
    prepare_image,
)
from ..trellis2.sampling import sample_flow_stage
from .conditioning import ConditioningView, encode_views, stage_condition
from .config import (
    CROP_PAD,
    DEFAULT_FOV_DEG,
    DEFAULT_TIER,
    EXPORT_FRAMES,
    LR_PROJECTION_GRID,
    PIXAL3D_TIERS,
    STAGE_CONDITIONING,
    VIEW_AZIMUTHS,
    VIEW_PAD,
    fov_radians,
)
from .projection import distance_from_fov, front_camera, orbit_camera, relative_cameras

__all__ = [
    "Pixal3DComponents",
    "Pixal3DViews",
    "prepare_views",
    "quantize_to_projection_grid",
    "reframe_volume",
    "resolve_projection_grid",
    "run_pixal3d",
]

_DECODER_GROWTH = 16
_CASCADE_UPSAMPLE_TIMES = 4
_CASCADE_LR_RESOLUTION = 512
_CASCADE_RESOLUTION_FLOOR = 1024
_CASCADE_RESOLUTION_STEP = 128
_CONDITION_SIZES = (512, 1024)

_FRAMES = {
    "upstream": ((0, -1), (2, 1), (1, 1)),
    "camera": ((0, 1), (2, -1), (1, 1)),
}


@dataclass
class Pixal3DComponents(Trellis2Components):
    naf: Any = None


@dataclass
class Pixal3DViews:
    views: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = sorted(set(self.views) - set(VIEW_AZIMUTHS))
        if unknown:
            raise ValueError(f"unknown view names {unknown}; expected some of {list(VIEW_AZIMUTHS)}")
        if self.views.get("front") is None:
            raise ValueError("multi-view reconstruction needs the front view; the rig is anchored on it")
        self.views = {name: self.views[name] for name in VIEW_AZIMUTHS if self.views.get(name) is not None}

    @property
    def names(self) -> List[str]:
        return list(self.views)


def quantize_to_projection_grid(
    coords: torch.Tensor, source_resolution: int, target_resolution: int
) -> torch.Tensor:
    grid = target_resolution // _DECODER_GROWTH
    quantised = torch.cat(
        [coords[:, :1], ((coords[:, 1:] + 0.5) / source_resolution * (grid - 1)).round().int()], dim=1
    )
    return quantised.unique(dim=0)


def resolve_projection_grid(
    coords: torch.Tensor,
    target_resolution: int,
    max_num_tokens: int,
    source_resolution: int = _CASCADE_LR_RESOLUTION,
) -> Tuple[torch.Tensor, int]:
    resolution = target_resolution
    while True:
        quantised = quantize_to_projection_grid(coords, source_resolution, resolution)
        if quantised.shape[0] < max_num_tokens or resolution <= _CASCADE_RESOLUTION_FLOOR:
            return quantised, resolution
        resolution -= _CASCADE_RESOLUTION_STEP


def _matte_view(image, matting):
    from PIL import Image

    if image.mode == "RGBA" and not bool(np.all(np.array(image)[:, :, 3] == 255)):
        matted = image
    elif matting is None:
        raise ValueError(
            "background removal needs a matting model and none is loaded: a view is fully opaque. "
            "Select a BiRefNet checkpoint, supply views with transparent backgrounds, or turn "
            "background removal off."
        )
    else:
        matted = matting(image.convert("RGB"))
    pixels = np.array(matted.convert("RGBA")).astype(np.float32) / 255.0
    return Image.fromarray((pixels[:, :, :3] * pixels[:, :, 3:4] * 255).astype(np.uint8))


def prepare_views(source, fov: float, *, matting=None, remove_background: bool = False) -> List[ConditioningView]:
    if isinstance(source, Pixal3DViews):
        distance = VIEW_PAD * distance_from_fov(fov)
        images = [
            _matte_view(image, matting) if remove_background else image for image in source.views.values()
        ]
        cameras = torch.stack([orbit_camera(VIEW_AZIMUTHS[name], 0.0, distance) for name in source.names])
        cameras = relative_cameras(cameras, float(cameras[0, :3, 3].norm()))
        return [ConditioningView(image=image, camera=camera) for image, camera in zip(images, cameras)]
    image = prepare_image(source, matting, pad=CROP_PAD) if remove_background else source
    return [ConditioningView(image=image, camera=front_camera(distance_from_fov(fov)))]


def reframe_volume(volume: MeshVolume, frame: str) -> MeshVolume:
    if frame not in _FRAMES:
        raise ValueError(f"unknown export frame {frame!r}; expected one of {list(EXPORT_FRAMES)}")
    axes = _FRAMES[frame]
    last = volume.resolution - 1
    vertices = torch.stack([volume.vertices[:, axis] * sign for axis, sign in axes], dim=-1)
    coords = torch.stack(
        [volume.coords[:, axis] if sign > 0 else last - volume.coords[:, axis] for axis, sign in axes], dim=-1
    )
    return replace(volume, vertices=vertices, coords=coords)


def _sample_sparse_structure(components, cond, neg, settings, device, generator, progress, is_cancelled):
    flow = components.ss_flow
    config = flow.config
    noise = torch.randn(1, config.in_channels, *([config.resolution] * 3), generator=generator).to(
        device=device, dtype=cond.dtype
    )
    with _on_device(flow, device):
        latent = sample_flow_stage(
            flow, noise, cond, neg, settings["sparse_structure"],
            on_step=_progress(progress, "sparse_structure"), is_cancelled=is_cancelled,
        )
    with _on_device(components.ss_vae, device) as decoder:
        occupancy = decoder(latent)
    return occupancy_to_coords(occupancy, LR_PROJECTION_GRID)


def _condition(components, encoded, stage_key, fov, device, *, coords=None, resolution):
    stage = STAGE_CONDITIONING[stage_key]
    if stage.naf_size is None:
        cond = stage_condition(encoded, stage, fov, coords=coords, resolution=resolution)
    else:
        with _on_device(components.naf, device) as naf:
            cond = stage_condition(encoded, stage, fov, coords=coords, resolution=resolution, naf=naf)
    return cond, cond.negative()


@torch.no_grad()
def run_pixal3d(
    components: Pixal3DComponents,
    source,
    *,
    tier: str = DEFAULT_TIER,
    fov_deg: float = DEFAULT_FOV_DEG,
    seed: int = 0,
    device: str | torch.device = "cuda",
    stage_settings: Optional[dict] = None,
    remove_background: bool = False,
    max_num_tokens: int = 49152,
    progress: Optional[Callable[[str, int, int], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> MeshVolume:
    if tier not in PIXAL3D_TIERS:
        raise ValueError(f"unknown Pixal3D resolution tier {tier!r}; expected one of {list(PIXAL3D_TIERS)}")
    if components.shape_flow_hr is None:
        raise ValueError(f"the {tier} tier is a cascade and needs the high-resolution shape flow")
    if components.naf is None:
        raise ValueError("Pixal3D conditions its shape and texture stages on NAF features; no NAF upsampler is loaded")

    fov = fov_radians(fov_deg)
    settings = {**STAGE_SAMPLING, **(stage_settings or {})}
    device = torch.device(device)
    generator = torch.Generator(device="cpu").manual_seed(int(seed))

    if remove_background and components.matting is not None:
        with _on_device(components.matting, device) as matting:
            views = prepare_views(source, fov, matting=matting, remove_background=True)
    else:
        views = prepare_views(source, fov, remove_background=remove_background)

    with _on_device(components.conditioner, device) as conditioner:
        encoded = encode_views(conditioner, views, _CONDITION_SIZES, device)

    cond, neg = _condition(
        components, encoded, "sparse_structure", fov, device, resolution=components.ss_flow.config.resolution
    )
    coords = _sample_sparse_structure(components, cond, neg, settings, device, generator, progress, is_cancelled)
    del cond, neg
    if coords.shape[0] == 0:
        raise ValueError(
            "the sparse-structure stage produced an empty volume: nothing in the input image was "
            "reconstructed as occupied space."
        )
    coords = coords.to(device)

    cond, neg = _condition(components, encoded, "shape_lr", fov, device, coords=coords, resolution=LR_PROJECTION_GRID)
    slat = _sample_slat(
        components.shape_flow_lr, coords, cond, neg, settings["shape"], device, generator,
        _progress(progress, "shape_lr"), is_cancelled,
    )
    del cond, neg
    slat = denormalize_slat(slat, SHAPE_SLAT_NORMALIZATION)

    with _on_device(components.shape_decoder, device) as decoder:
        upsampled = decoder.upsample(slat, upsample_times=_CASCADE_UPSAMPLE_TIMES)
    hr_coords, resolution = resolve_projection_grid(upsampled, int(tier), max_num_tokens)
    hr_coords = hr_coords.to(device)
    grid = resolution // _DECODER_GROWTH

    cond, neg = _condition(components, encoded, "shape_hr", fov, device, coords=hr_coords, resolution=grid)
    shape_slat = _sample_slat(
        components.shape_flow_hr, hr_coords, cond, neg, settings["shape"], device, generator,
        _progress(progress, "shape_hr"), is_cancelled,
    )
    del cond, neg
    shape_slat = denormalize_slat(shape_slat, SHAPE_SLAT_NORMALIZATION)

    cond, neg = _condition(components, encoded, "texture", fov, device, coords=shape_slat.coords, resolution=grid)
    tex_slat = _sample_texture(components, cond, neg, shape_slat, settings, device, generator, progress, is_cancelled)
    del cond, neg, encoded

    volume = _decode(components, shape_slat, tex_slat, resolution, device, progress)
    volume.cond_image = views[0].image
    volume.tex_slat = tex_slat
    return volume
