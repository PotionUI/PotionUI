from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, List, Optional

from src.pipelines.contracts import IOType, PipeConfigSpec, PipeInput, PipeInputSpec
from src.pipelines.pipes.generator.trellis2.main import GeneratorTrellis2Pipe
from src.platform.runtime.native.arch.pixal3d.config import (
    DEFAULT_FOV_DEG,
    EXPORT_FRAMES,
    MULTIVIEW,
    VIEW_AZIMUTHS,
)
from src.platform.runtime.native.arch.pixal3d.image_to_mesh import (
    Pixal3DViews,
    reframe_volume,
    run_pixal3d,
)

_SIDE_VIEWS = tuple(name for name in VIEW_AZIMUTHS if name != "front")


class GeneratorPixal3DPipe(GeneratorTrellis2Pipe):
    name = "generator"
    description = "Native Pixal3D generator (one image, or a front/left/back/right rig, to a textured GLB mesh)"

    _family_pipe = "generator/pixal3d"
    _loader_pipe = "model_loader/pixal3d"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {**super().get_default_config(), "camera_fov": DEFAULT_FOV_DEG, "export_frame": EXPORT_FRAMES[0]}

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            *super().configuration(),
            PipeConfigSpec("camera_fov", float, DEFAULT_FOV_DEG,
                           "Horizontal field of view of the input camera in degrees. It sets the camera "
                           "distance every voxel is projected from, so a wrong value misplaces the samples.",
                           required=False, min_value=1.0, max_value=170.0),
            PipeConfigSpec("export_frame", str, EXPORT_FRAMES[0],
                           "Orientation of the exported mesh. upstream applies Pixal3D's own (x, y, z) -> "
                           "(-x, y, -z); camera keeps the frame as decoded (ComfyUI's choice). The two differ "
                           "by a 180 degree turn about the vertical axis.",
                           required=False, choices=list(EXPORT_FRAMES)),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "Pixal3D model bundle", is_array=False),
            PipeInputSpec("image", IOType.IMAGE, True,
                          "Source image(s); with a multi-view bundle, the front view", is_array=True),
            *[
                PipeInputSpec(view, IOType.IMAGE, False, f"The {view} view of the multi-view rig", is_array=True)
                for view in _SIDE_VIEWS
            ],
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds, one per mesh", is_array=True),
        ]

    def _sources(self, pipe_input: PipeInput) -> List[Any]:
        images = super()._sources(pipe_input)
        bundle = pipe_input.input.get("model")
        sides = {view: list(pipe_input.input.get(view) or []) for view in _SIDE_VIEWS}
        if getattr(bundle, "variant", None) != MULTIVIEW:
            given = [view for view, items in sides.items() if items]
            if given:
                raise ValueError(
                    f"{', '.join(given)} view(s) were wired in, but the loaded Pixal3D bundle is the "
                    "single-view one; multi-view reconstruction needs pixal3d_multiview_bf16.safetensors"
                )
            return images
        if not images:
            return []
        if len(images) > 1:
            raise ValueError("a multi-view reconstruction takes exactly one front view")
        views = {"front": images[0], **{view: items[0] for view, items in sides.items() if items}}
        return [Pixal3DViews(views)]

    def _reconstruct(self, components, image, **kwargs):
        return run_pixal3d(components, image, fov_deg=float(self.config.get("camera_fov", DEFAULT_FOV_DEG)), **kwargs)

    @staticmethod
    def _source_id(image, seed: int, tier: str) -> str:
        if not isinstance(image, Pixal3DViews):
            return GeneratorTrellis2Pipe._source_id(image, seed, tier)
        digest = hashlib.sha1()
        try:
            for name, view in image.views.items():
                digest.update(name.encode())
                digest.update(view.tobytes())
            value = digest.hexdigest()[:16]
        except Exception:
            value = "unknown"
        return f"{value}:views={'+'.join(image.names)}:seed={int(seed)}:tier={tier}"

    def _export(
        self,
        volume,
        is_cancelled: Optional[Callable[[], bool]] = None,
        source_id: str = "",
    ) -> str:
        frame = str(self.config.get("export_frame", EXPORT_FRAMES[0]))
        return super()._export(reframe_volume(volume, frame), is_cancelled, source_id=source_id)
