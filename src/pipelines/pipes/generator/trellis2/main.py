"""Generator for the native TRELLIS.2 family (image to textured mesh).

Consumes the bundle from ``model_loader/trellis2`` and one or more source
images, runs the three-stage cascade
(:func:`~src.platform.runtime.native.arch.trellis2.image_to_mesh.run_image_to_mesh`),
and bakes each result into a ``.glb`` with PBR textures. The resolution tier
lives on the bundle rather than in this pipe's config: it decides which flow
models the loader acquires, so having it configurable in two places is a
mismatch waiting to happen.

Two things about this family shape the code here.

**Every model is offloaded before the bake.** The post-process chain — clean,
decimate, ``xatlas`` unwrap, texture bake — is CPU-bound and allocates heavily,
while the models it follows hold 12-24GB of VRAM at the upper tiers. The run
returns its models to CPU stage by stage; this pipe empties the CUDA cache
before the bake so the two phases never overlap.

**The bake dominates the wall clock, and ``decimation_target`` is why.**
``xatlas`` is CPU-only and scales worse than linearly — roughly 12s at 50k
faces, many minutes at 200k — so the default here is far below upstream's
GPU-sized 1M. See ``arch/trellis2/postprocess.py``'s module docstring.

**Cancellation is checked at cooperative boundaries and while the bake runs.**
``is_cancelled`` is polled before each image starts, threaded into the cascade
so its sampling loops can stop mid-step, polled again before the bake, and
polled while the GIL-holding mesh cleanup and UV unwrap run in the offload
worker process (``src/platform/runtime/offload``). A cancellation observed
there kills that worker and raises
:class:`~src.platform.runtime.native.errors.SamplingCancelled`; the server
process is never touched.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import torch

from src.pipelines.contracts import (
    BasePipe,
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.outputs import (
    GalleryGenerationOutput,
    Icon,
    MeshGenerationOutput,
    Progress,
    ProgressGenerationOutput,
    SeedGenerationOutput,
)
from src.platform.observability.profiling import get_profiler, profiling_enabled
from src.platform.runtime.native.arch.trellis2.config import STAGE_SAMPLING, TEX_SLAT_NORMALIZATION, StageSampling
from src.platform.runtime.native.arch.trellis2.image_to_mesh import AABB, normalize_slat, run_image_to_mesh
from src.platform.runtime.native.arch.trellis2.postprocess import PBR_ATTR_LAYOUT, postprocess_to_glb
from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.util.latents import generate_seed

#: Stage key -> the line shown while it runs. ``shape_lr``/``shape_hr`` are the
#: cascade's two passes; a 512-tier run reports one ``shape`` stage instead.
_STAGE_LABELS = {
    "sparse_structure": "Building the sparse structure",
    "shape": "Sampling shape",
    "shape_lr": "Sampling shape (low resolution)",
    "shape_hr": "Sampling shape (high resolution)",
    "texture": "Sampling PBR texture",
    "decode": "Decoding geometry and texture",
}

#: Per-stage sampler settings the form may override, and their bounds.
_STAGE_BOUNDS = {
    "steps": (1, 50),
    "guidance_strength": (0.0, 10.0),
    "guidance_rescale": (0.0, 1.0),
    "rescale_t": (1.0, 6.0),
}

_STAGE_LABELS_SHORT = {
    "sparse_structure": "sparse-structure",
    "shape": "shape",
    "texture": "PBR texture",
}


logger = logging.getLogger(__name__)


class GeneratorTrellis2Pipe(BasePipe):
    name = "generator"
    description = "Native TRELLIS.2 generator (single image to a textured GLB mesh)"

    # -- declaration -------------------------------------------------------

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        config = {
            "seed": -1,
            "remove_background": False,
            "decimation_target": 100000,
            "texture_size": 2048,
            "max_num_tokens": 49152,
            "project_to_source": True,
            "remesh": True,
            "device": "cuda",
        }
        for stage, defaults in STAGE_SAMPLING.items():
            config[f"{stage}_steps"] = defaults.steps
            config[f"{stage}_guidance_strength"] = defaults.guidance_strength
            config[f"{stage}_guidance_rescale"] = defaults.guidance_rescale
            config[f"{stage}_rescale_t"] = defaults.rescale_t
        return config

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        specs = [
            PipeConfigSpec("seed", int, -1, "Random seed; -1 draws a fresh one per image",
                           required=False, min_value=-1),
            PipeConfigSpec("remove_background", bool, False,
                           "Crop and centre the subject before reconstruction. An image with a "
                           "transparent background uses its own alpha; an opaque one needs the "
                           "loader's matting model and fails with an explanation without it.",
                           required=False),
            PipeConfigSpec("decimation_target", int, 100000,
                           "Face budget the exported mesh is decimated to. The UV unwrap is "
                           "CPU-bound and scales worse than linearly, so this dominates export "
                           "time above ~100k.",
                           required=False, min_value=5000, max_value=1000000),
            PipeConfigSpec("texture_size", int, 2048, "Edge length of the baked PBR texture maps",
                           required=False, choices=[1024, 2048, 4096]),
            PipeConfigSpec("max_num_tokens", int, 49152,
                           "Token budget for the cascade's high-resolution shape pass. A shape "
                           "too detailed for the tier is decoded at a coarser one instead of "
                           "failing.",
                           required=False, min_value=4096, max_value=262144),
            PipeConfigSpec("project_to_source", bool, True,
                           "Push baked texels back onto the pre-decimation surface before "
                           "sampling colour, so decimated faces that drift off the voxel shell "
                           "do not read empty space.", required=False),
            PipeConfigSpec("remesh", bool, True,
                           "Rebuild the decoded surface as a closed, consistently wound mesh "
                           "(narrow-band dual contouring one voxel off the surface) before "
                           "decimating, as upstream does. Off keeps the decoded topology, "
                           "voxel-scale tears included.", required=False),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False,
                           choices=["cuda", "cpu"]),
        ]

        for stage, defaults in STAGE_SAMPLING.items():
            label = _STAGE_LABELS_SHORT[stage]
            specs.append(PipeConfigSpec(
                f"{stage}_steps", int, defaults.steps, f"Sampling steps for the {label} stage",
                required=False, min_value=_STAGE_BOUNDS["steps"][0], max_value=_STAGE_BOUNDS["steps"][1],
            ))
            for field, value in (
                ("guidance_strength", defaults.guidance_strength),
                ("guidance_rescale", defaults.guidance_rescale),
                ("rescale_t", defaults.rescale_t),
            ):
                low, high = _STAGE_BOUNDS[field]
                specs.append(PipeConfigSpec(
                    f"{stage}_{field}", float, value,
                    f"{field.replace('_', ' ').capitalize()} for the {label} stage",
                    required=False, min_value=low, max_value=high,
                ))
        return specs

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "TRELLIS.2 model bundle", is_array=False),
            PipeInputSpec("image", IOType.IMAGE, True, "Source image(s) to reconstruct", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds, one per image", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("mesh", IOType.MESH, "Paths to the generated .glb files", is_array=True),
            PipeOutputSpec("seed", IOType.SEED, "Seed each mesh was generated with", is_array=True),
        ]

    # -- run ---------------------------------------------------------------

    def process(
            self,
            pipe_input: PipeInput,
            generation_outputs: callable,
            is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> PipeOutput:
        is_cancelled = is_cancelled or (lambda: False)
        images = pipe_input.input.get("image") or []
        if not images:
            raise ValueError("generator/trellis2 needs a source image, but none was provided")

        bundle = pipe_input.input.get("model")
        if bundle is None:
            raise ValueError("generator/trellis2 needs the model bundle from model_loader/trellis2")

        components = bundle.components()
        tier = getattr(bundle, "tier", "1024")
        device = self.config.get("device") or getattr(bundle, "device", "cuda")
        seeds = self._seeds(pipe_input, len(images))
        stage_settings = self._stage_settings()

        mesh_paths: List[str] = []
        meshes: List[MeshGenerationOutput] = []

        try:
            for index, (image, seed) in enumerate(zip(images, seeds)):
                if is_cancelled():
                    raise SamplingCancelled()

                generation_outputs(SeedGenerationOutput(index=index, seed=seed))
                volume = run_image_to_mesh(
                    components,
                    image,
                    tier=tier,
                    seed=seed,
                    device=device,
                    stage_settings=stage_settings,
                    remove_background=bool(self.config.get("remove_background", False)),
                    max_num_tokens=int(self.config.get("max_num_tokens", 49152)),
                    progress=self._progress(generation_outputs, index, len(images)),
                    is_cancelled=is_cancelled,
                )

                # The models are back on CPU by now, but their freed blocks are
                # still in torch's cache; the bake wants that memory back.
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

                if is_cancelled():
                    # postprocess_to_glb is not itself interruptible -- this is
                    # the last cooperative boundary before paying for it.
                    raise SamplingCancelled()

                generation_outputs(ProgressGenerationOutput(
                    state="Baking PBR materials", icon=Icon(name="cube", effect="pulse"),
                    progress=Progress(index, len(images)),
                ))
                mesh_path = self._export(volume, is_cancelled, source_id=self._source_id(image, seed, tier))
                mesh_paths.append(mesh_path)
                meshes.append(MeshGenerationOutput(
                    mesh_path=mesh_path,
                    temporary=False,
                    seed=seed,
                    vertex_count=int(volume.vertices.shape[0]),
                    face_count=int(volume.faces.shape[0]),
                ))

            if is_cancelled():
                raise SamplingCancelled()
        except SamplingCancelled:
            # Whether raised by one of the polls above or propagated straight
            # out of a sampling stage inside run_image_to_mesh, no gallery
            # follows: every bake already accumulated in this batch is now
            # unreachable and would otherwise leak on disk.
            self._discard_exports(mesh_paths)
            raise

        generation_outputs(GalleryGenerationOutput(images=[], meshes=meshes))
        generation_outputs(ProgressGenerationOutput(
            state="Reconstructed the mesh" if len(meshes) == 1
            else f"Reconstructed <<NUMBER:{len(meshes)}>> meshes",
            icon=Icon(name="check"),
        ))
        return PipeOutput(output={"mesh": mesh_paths, "seed": seeds})

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _discard_exports(mesh_paths: List[str]) -> None:
        """Best-effort delete every ``.glb`` this batch has baked so far.

        Called once, from the single ``except SamplingCancelled`` around the
        per-image loop, so it runs the same way whether cancellation was
        caught by one of this pipe's own polls or raised from inside a
        sampling stage deep in ``run_image_to_mesh``.
        """
        for path in mesh_paths:
            try:
                os.remove(path)
            except OSError:
                pass

    @staticmethod
    def _source_id(image, seed: int, tier: str) -> str:
        try:
            digest = hashlib.sha1(image.tobytes()).hexdigest()[:16]
        except Exception:
            digest = "unknown"
        return f"{digest}:seed={int(seed)}:tier={tier}"

    @staticmethod
    def _dump_raw_mesh(volume, source_id: str) -> Optional[str]:
        if not profiling_enabled():
            return None
        out_dir = get_profiler().out_dir
        if out_dir is None:
            return None
        path = os.path.join(str(out_dir), "raw_mesh.npz")
        try:
            np.savez(
                path,
                vertices=volume.vertices.detach().cpu().numpy(),
                faces=volume.faces.detach().cpu().numpy(),
                source_id=np.array(source_id),
                resolution=np.array(int(volume.resolution)),
            )
        except Exception:
            logger.warning("TRELLIS.2 raw mesh dump to %s failed", path, exc_info=True)
            return None
        return path

    @staticmethod
    def _dump_texture_volume(volume, source_id: str) -> Optional[str]:
        if not profiling_enabled():
            return None
        out_dir = get_profiler().out_dir
        if out_dir is None:
            return None
        path = os.path.join(str(out_dir), "texture_volume.npz")
        try:
            np.savez(
                path,
                coords=volume.coords.detach().cpu().numpy().astype(np.int32),
                feats=volume.attrs.detach().cpu().numpy().astype(np.float16),
                source_id=np.array(source_id),
                resolution=np.array(int(volume.resolution)),
                voxel_size=np.array(float(volume.voxel_size)),
                aabb=np.array(AABB, dtype=np.float32),
                layout=np.array(list(PBR_ATTR_LAYOUT)),
            )
        except Exception:
            logger.warning("TRELLIS.2 texture volume dump to %s failed", path, exc_info=True)
            return None
        return path

    @staticmethod
    def _dump_conditioning(volume, source_id: str) -> Optional[str]:
        if not profiling_enabled():
            return None
        out_dir = get_profiler().out_dir
        if out_dir is None:
            return None
        path = os.path.join(str(out_dir), "tex_slat.npz")
        try:
            if volume.cond_image is not None:
                volume.cond_image.convert("RGB").save(os.path.join(str(out_dir), "cond_image.png"))
            if volume.tex_slat is None:
                return None
            after = volume.tex_slat
            before = normalize_slat(after, TEX_SLAT_NORMALIZATION)
            np.savez(
                path,
                coords=after.coords.detach().cpu().numpy().astype(np.int32),
                feats_normalised=before.feats.detach().float().cpu().numpy().astype(np.float16),
                feats_denormalised=after.feats.detach().float().cpu().numpy().astype(np.float16),
                source_id=np.array(source_id),
            )
        except Exception:
            logger.warning("TRELLIS.2 conditioning dump to %s failed", path, exc_info=True)
            return None
        return path

    def _seeds(self, pipe_input: PipeInput, count: int) -> List[int]:
        """One seed per image: the wired ``seed`` input, else the config's.

        A configured seed is offset per image the way ``seed_generator`` does,
        so a batch is reproducible without every mesh being identical.
        """
        provided = pipe_input.input.get("seed") or []
        if provided:
            return [int(provided[i % len(provided)]) for i in range(count)]

        configured = int(self.config.get("seed", -1))
        if configured < 0:
            return [generate_seed() for _ in range(count)]
        return [configured + i for i in range(count)]

    def _stage_settings(self) -> Dict[str, StageSampling]:
        """The published per-stage defaults with this pipe's overrides on top.

        ``guidance_interval`` and ``sigma_min`` are not exposed: they are not
        parameters a user tunes, and omitting one is a different failure from
        passing a wrong one.
        """
        settings = {}
        for stage, defaults in STAGE_SAMPLING.items():
            settings[stage] = StageSampling(
                steps=int(self.config.get(f"{stage}_steps", defaults.steps)),
                guidance_strength=float(
                    self.config.get(f"{stage}_guidance_strength", defaults.guidance_strength)),
                guidance_rescale=float(
                    self.config.get(f"{stage}_guidance_rescale", defaults.guidance_rescale)),
                guidance_interval=defaults.guidance_interval,
                rescale_t=float(self.config.get(f"{stage}_rescale_t", defaults.rescale_t)),
                sigma_min=defaults.sigma_min,
            )
        return settings

    @staticmethod
    def _progress(generation_outputs: callable, index: int, total: int):
        def report(stage: str, step: int, steps: int) -> None:
            label = _STAGE_LABELS.get(stage, stage)
            if total > 1:
                label = f"{label} (<<NUMBER:{index + 1}>> of <<NUMBER:{total}>>)"
            generation_outputs(ProgressGenerationOutput(
                state=label, icon=Icon(name="cube", effect="pulse"),
                progress=Progress(step, steps),
            ))

        return report

    def _export(
        self,
        volume,
        is_cancelled: Optional[Callable[[], bool]] = None,
        source_id: str = "",
    ) -> str:
        """Bake ``volume`` into a ``.glb`` and return its path.

        ``volume.voxel_size`` is the texture grid's own, which after a
        token-budget degrade is not the tier that was asked for — passing the
        requested tier instead would sample the attribute volume off-grid and
        texture the mesh with the wrong voxels.

        The GIL-holding cleanup and UV unwrap run in a separate worker
        process so the server's event loop keeps being served; ``is_cancelled``
        is polled while they run and a cancellation kills that worker.
        """
        out_path = tempfile.NamedTemporaryFile(suffix=".glb", delete=False).name
        self._dump_raw_mesh(volume, source_id)
        self._dump_texture_volume(volume, source_id)
        self._dump_conditioning(volume, source_id)
        try:
            postprocess_to_glb(
                vertices=volume.vertices,
                faces=volume.faces,
                attr_volume=volume.attrs,
                coords=volume.coords,
                voxel_size=volume.voxel_size,
                decimation_target=int(self.config.get("decimation_target", 100000)),
                texture_size=int(self.config.get("texture_size", 2048)),
                out_path=out_path,
                project_to_source=bool(self.config.get("project_to_source", True)),
                is_cancelled=is_cancelled,
                remesh=bool(self.config.get("remesh", True)),
            )
        except BaseException as exc:
            if os.path.exists(out_path):
                os.unlink(out_path)
            if not isinstance(exc, ValueError):
                raise
            raise ValueError(
                f"TRELLIS.2 produced no exportable geometry: {exc}. This usually means the "
                "source image did not give the sparse-structure stage a solid subject to "
                "reconstruct — try an image with a clearer, better-separated subject."
            ) from exc
        return out_path
