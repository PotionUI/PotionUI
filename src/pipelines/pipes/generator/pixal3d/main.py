from __future__ import annotations

import hashlib
import logging
from typing import Any, Callable, Dict, List, Optional

from src.pipelines.contracts import IOType, PipeConfigSpec, PipeInput, PipeInputSpec
from src.pipelines.outputs import TextGenerationOutput
from src.pipelines.pipes.generator.trellis2.main import GeneratorTrellis2Pipe
from src.platform.runtime.native.arch.pixal3d.config import (
    DEFAULT_FOV_DEG,
    EXPORT_FRAMES,
    FOV_AUTO,
    FOV_ESTIMATED,
    FOV_MANUAL,
    FOV_MODES,
    MULTIVIEW,
    VIEW_AZIMUTHS,
)
from src.platform.runtime.native.arch.pixal3d.image_to_mesh import (
    CameraFov,
    Pixal3DViews,
    reframe_volume,
    run_pixal3d,
)

logger = logging.getLogger(__name__)

_SIDE_VIEWS = tuple(name for name in VIEW_AZIMUTHS if name != "front")


class GeneratorPixal3DPipe(GeneratorTrellis2Pipe):
    name = "generator"
    description = "Native Pixal3D generator (one image, or a front/left/back/right rig, to a textured GLB mesh)"

    _family_pipe = "generator/pixal3d"
    _loader_pipe = "model_loader/pixal3d"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            **super().get_default_config(),
            "camera_fov_mode": FOV_MANUAL,
            "camera_fov": DEFAULT_FOV_DEG,
            "export_frame": EXPORT_FRAMES[0],
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            *super().configuration(),
            PipeConfigSpec("camera_fov_mode", str, FOV_MANUAL,
                           "manual projects through camera_fov; auto estimates the horizontal FOV of the "
                           "(front) input image with the loader's MoGe-2 camera estimator, as ComfyUI's "
                           "Pixal3D workflow does, and falls back to camera_fov when no focal length can "
                           "be recovered.",
                           required=False, choices=list(FOV_MODES)),
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

    def _fov_mode(self) -> str:
        mode = str(self.config.get("camera_fov_mode") or FOV_MANUAL)
        if mode not in FOV_MODES:
            raise ValueError(f"unknown camera FOV mode {mode!r}; expected one of {list(FOV_MODES)}")
        return mode

    def _reconstruct(self, components, image, generation_outputs, index, **kwargs):
        estimate = self._fov_mode() == FOV_AUTO
        if estimate and getattr(components, "camera_estimator", None) is None:
            raise ValueError(
                "Camera FOV is set to Auto (MoGe-2), but no camera estimator was loaded. Select "
                "moge_2_vitl_normal_fp16.safetensors (Comfy-Org/MoGe, geometry_estimation/) as the Camera "
                "Estimator under Models, or set Camera FOV to Manual."
            )
        return run_pixal3d(
            components,
            image,
            fov_deg=float(self.config.get("camera_fov", DEFAULT_FOV_DEG)),
            estimate_fov=estimate,
            on_camera_fov=self._camera_fov_reporter(generation_outputs, index) if estimate else None,
            **kwargs,
        )

    @staticmethod
    def _camera_fov_reporter(generation_outputs, index: int) -> Callable[[CameraFov], None]:
        def report(camera: CameraFov) -> None:
            if camera.source == FOV_ESTIMATED:
                text = f"{camera.degrees:.2f}° horizontal, estimated by MoGe-2"
                logger.info("Pixal3D camera FOV for image %d estimated by MoGe-2: %.2f deg", index, camera.degrees)
            else:
                text = (
                    f"{camera.degrees:.2f}° horizontal (the manual value): MoGe-2 recovered no focal length "
                    "from this image"
                )
                logger.warning(
                    "Pixal3D camera FOV for image %d: MoGe-2 recovered no focal length, using %.2f deg",
                    index, camera.degrees,
                )
            generation_outputs(TextGenerationOutput(title="Camera FOV", text=text, index=index))

        return report

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
