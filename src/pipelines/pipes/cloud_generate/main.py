import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from src.pipelines.cloud import (
    BlockingCloudRunner,
    CloudRunCancelled,
    CloudRunOutcome,
    CloudRunProgress,
    CloudRunRequest,
)
from src.pipelines.contracts import (
    BasePipe,
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.outputs import GenerationExecutionError, Icon, ParamGenerationOutput, Progress, ProgressGenerationOutput

DEFAULT_ROLES = {
    "images": "reference",
    "first_frame": "first_frame",
    "last_frame": "last_frame",
    "video": "source_video",
    "audio": "source_audio",
}
RANDOM_SEED = -1


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _prompt_pair(entry: Any, shared_negative: str) -> Tuple[str, str]:
    if isinstance(entry, dict):
        return str(entry.get("positive") or ""), str(entry.get("negative") or shared_negative)
    return str(entry or ""), shared_negative


def _progress_text(progress: CloudRunProgress) -> str:
    if progress.message:
        return progress.message
    if progress.state == "queued":
        if progress.queue_position is not None:
            return f"Waiting at the provider, position {progress.queue_position}"
        return "Waiting at the provider"
    if progress.state == "fetching":
        return "Downloading the result"
    return f"Generating <<TIME:{progress.elapsed_s:.0f}s>>"


class CloudGeneratePipe(BasePipe):
    name = "cloud_generate"
    description = "Generates media with a model hosted by a cloud provider"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "task": "txt2img",
            "model": "",
            "prompts": [],
            "negative_prompt": "",
            "quantity": 1,
            "params": {},
            "options": {},
            "roles": {},
            "cloud": {},
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("task", str, "txt2img", "What the model is asked to do, such as txt2img or img2video"),
            PipeConfigSpec("model", str, "", "Slug of the cloud model to run", required=True),
            PipeConfigSpec("prompts", list, [], "One prompt per image, or a single prompt for all of them"),
            PipeConfigSpec("negative_prompt", str, "", "Negative prompt shared by every image"),
            PipeConfigSpec("quantity", int, 1, "How many outputs to generate", min_value=1, max_value=64),
            PipeConfigSpec("params", dict, {}, "Canonical generation parameters passed to the provider"),
            PipeConfigSpec("options", dict, {}, "Provider options chosen on the form, merged under params"),
            PipeConfigSpec("roles", dict, {}, "Media role for each media input, keyed by input name"),
            PipeConfigSpec("cloud", dict, {}, "Backend identity, filled in by the cloud backend"),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("CLOUD", IOType.SERVICE, True, "Cloud run service bound to this generation"),
            PipeInputSpec("images", IOType.IMAGE, False, "Input images", True),
            PipeInputSpec("first_frame", IOType.IMAGE, False, "First frame of a video"),
            PipeInputSpec("last_frame", IOType.IMAGE, False, "Last frame of a video"),
            PipeInputSpec("video", IOType.VIDEO, False, "Input video files", True),
            PipeInputSpec("audio", IOType.AUDIO, False, "Input audio files", True),
            PipeInputSpec("seed", IOType.SEED, False, "Seeds for the outputs", True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Generated images", True),
            PipeOutputSpec("video", IOType.VIDEO, "Generated video files", True),
            PipeOutputSpec("audio", IOType.AUDIO, "Generated audio files", True),
            PipeOutputSpec("seed", IOType.SEED, "Seeds of the generated outputs", True),
        ]

    def process(self, pipe_input: PipeInput, generation_outputs: callable, is_cancelled: Optional[callable] = None) -> PipeOutput:
        runner: Optional[BlockingCloudRunner] = pipe_input.input.get("CLOUD")
        if runner is None:
            raise GenerationExecutionError("Cloud generation is not available for this backend.")

        model = str(self.config.get("model") or "")
        if not model:
            raise GenerationExecutionError("No cloud model was selected.")

        scratch = Path(tempfile.mkdtemp(prefix="potionui-cloud-in-"))
        try:
            inputs = self._stage_inputs(pipe_input, scratch)
            requests = self._requests(model, pipe_input, inputs)

            generation_outputs(ParamGenerationOutput(name="model", values=[model] * sum(r.count for r in requests)))

            def report(progress: CloudRunProgress) -> None:
                fraction = progress.fraction
                generation_outputs(ProgressGenerationOutput(
                    state=_progress_text(progress),
                    icon=Icon("play", "beat"),
                    progress=Progress(current=int(fraction * 100), max=100) if fraction is not None else None,
                ))

            images: List[Image.Image] = []
            videos: List[str] = []
            audios: List[str] = []
            seeds: List[int] = []
            try:
                for request in requests:
                    outcome = runner.run_blocking(request, on_progress=report, is_cancelled=is_cancelled)
                    self._collect(outcome, request, images, videos, audios, seeds)
            except CloudRunCancelled:
                self._discard(videos, audios)
                return PipeOutput(output={"image": [], "video": [], "audio": [], "seed": []})
            except BaseException:
                self._discard(videos, audios)
                raise
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

        return PipeOutput(output={"image": images, "video": videos, "audio": audios, "seed": seeds})

    def _stage_inputs(self, pipe_input: PipeInput, scratch: Path) -> Dict[str, List[Path]]:
        roles = {**DEFAULT_ROLES, **(self.config.get("roles") or {})}
        staged: Dict[str, List[Path]] = {}
        for input_name, role in roles.items():
            for index, value in enumerate(_as_list(pipe_input.input.get(input_name))):
                if isinstance(value, Image.Image):
                    path = scratch / f"{input_name}_{index}.png"
                    value.save(path, format="PNG")
                else:
                    path = Path(str(value))
                staged.setdefault(role, []).append(path)
        return staged

    def _requests(self, model: str, pipe_input: PipeInput, inputs: Dict[str, List[Path]]) -> List[CloudRunRequest]:
        quantity = max(1, int(self.config.get("quantity") or 1))
        shared_negative = str(self.config.get("negative_prompt") or "")
        prompts = [_prompt_pair(entry, shared_negative) for entry in _as_list(self.config.get("prompts"))] or [("", shared_negative)]
        seeds = [int(seed) for seed in _as_list(pipe_input.input.get("seed")) if seed is not None]
        options = self.config.get("options")
        params = {**(options if isinstance(options, dict) else {}), **(self.config.get("params") or {})}
        params = {name: value for name, value in params.items() if value is not None and value != ""}
        task = str(self.config.get("task") or "txt2img")

        def build(prompt: Tuple[str, str], count: int, seed: Optional[int]) -> CloudRunRequest:
            return CloudRunRequest(
                task=task,
                model=model,
                prompt=prompt[0],
                negative_prompt=prompt[1] or None,
                seed=None if seed is None or seed == RANDOM_SEED else seed,
                count=count,
                params=params,
                inputs=inputs,
            )

        if len(set(prompts)) == 1:
            return [build(prompts[0], quantity, seeds[0] if seeds else None)]
        return [
            build(prompts[index % len(prompts)], 1, seeds[index] if index < len(seeds) else None)
            for index in range(quantity)
        ]

    @staticmethod
    def _discard(videos: List[str], audios: List[str]) -> None:
        for path in (*videos, *audios):
            Path(path).unlink(missing_ok=True)

    @staticmethod
    def _collect(
        outcome: CloudRunOutcome,
        request: CloudRunRequest,
        images: List[Image.Image],
        videos: List[str],
        audios: List[str],
        seeds: List[int],
    ) -> None:
        produced = 0
        for artifact in sorted(outcome.artifacts, key=lambda item: item.index):
            if artifact.modality == "image":
                with Image.open(artifact.path) as opened:
                    opened.load()
                    images.append(opened.copy())
                artifact.path.unlink(missing_ok=True)
            elif artifact.modality == "video":
                videos.append(str(artifact.path))
            else:
                audios.append(str(artifact.path))
            produced += 1
        seed = outcome.seed_used if outcome.seed_used is not None else request.seed
        if seed is not None:
            seeds.extend(seed + offset for offset in range(produced))
