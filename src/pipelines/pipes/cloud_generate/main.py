import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from src.pipelines.cloud import (
    BlockingCloudRunner,
    CloudRunCancelled,
    CloudRunError,
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
from src.pipelines.outputs import (
    CostGenerationOutput,
    DirectorShotGenerationOutput,
    GalleryGenerationOutput,
    GenerationExecutionError,
    Icon,
    ParamGenerationOutput,
    Progress,
    ProgressGenerationOutput,
    VideoGenerationOutput,
)
from src.pipelines.pipes._shared.media.frame_extract import extract_frame
from src.pipelines.pipes.cloud_generate.director import CloudShot, kept_text, plan_shots, stitch_wanted
from src.pipelines.pipes.cloud_generate.stitch import StitchError, stitch_clips

DEFAULT_ROLES = {
    "images": "reference",
    "first_frame": "first_frame",
    "last_frame": "last_frame",
    "video": "source_video",
    "audio": "source_audio",
}
RANDOM_SEED = -1
SHOT_PARAM = "duration_s"

logger = logging.getLogger(__name__)


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
            "director": None,
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
            PipeConfigSpec("director", dict, None, "Video Director document; when set, every shot is one request and the clips are joined"),
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

        director = self.config.get("director")
        if isinstance(director, dict) and director.get("segments"):
            return self._direct(runner, model, director, generation_outputs, is_cancelled)

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
                    generation_outputs(CostGenerationOutput(
                        model=request.model,
                        amount_usd=outcome.cost.amount_usd if outcome.cost else None,
                        source=outcome.cost.source if outcome.cost else "provider",
                        task=request.task,
                        count=request.count,
                        params=dict(request.params),
                        outputs=len(outcome.artifacts),
                    ))
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
        params = self._params()
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
                user_ref=str((self.config.get("cloud") or {}).get("user_ref") or ""),
            )

        if len(set(prompts)) == 1:
            return [build(prompts[0], quantity, seeds[0] if seeds else None)]
        return [
            build(prompts[index % len(prompts)], 1, seeds[index] if index < len(seeds) else None)
            for index in range(quantity)
        ]

    def _params(self) -> Dict[str, Any]:
        options = self.config.get("options")
        params = {**(options if isinstance(options, dict) else {}), **(self.config.get("params") or {})}
        return {name: value for name, value in params.items() if value is not None and value != ""}

    def _direct(
        self,
        runner: BlockingCloudRunner,
        model: str,
        document: Dict[str, Any],
        generation_outputs: callable,
        is_cancelled: Optional[callable],
    ) -> PipeOutput:
        try:
            shots = plan_shots(document)
        except ValueError as error:
            raise GenerationExecutionError(str(error)) from None
        base_params = self._params()
        user_ref = str((self.config.get("cloud") or {}).get("user_ref") or "")
        film = len(shots) > 1
        generation_outputs(ParamGenerationOutput(name="model", values=[model] * len(shots)))

        def shot_state(shot: CloudShot, status: str, **fields: Any) -> None:
            if film:
                generation_outputs(DirectorShotGenerationOutput(
                    shot_id=shot.segment_id, status=status, shot_index=shot.index, shot_count=shot.count, **fields,
                ))

        def skip_after(shot: Optional[CloudShot]) -> None:
            for later in shots[(shot.index + 1) if shot is not None else 0:]:
                shot_state(later, "skipped")

        for shot in shots:
            shot_state(shot, "queued")

        scratch = Path(tempfile.mkdtemp(prefix="potionui-cloud-shots-"))
        clips: List[str] = []
        seeds: List[int] = []
        current: Optional[CloudShot] = None
        try:
            for shot in shots:
                if is_cancelled is not None and is_cancelled():
                    skip_after(current)
                    return PipeOutput(output={"image": [], "video": [], "audio": [], "seed": []})
                current = shot
                self._announce(shot, generation_outputs)
                shot_state(shot, "generating")
                try:
                    start = shot.start_image
                    if shot.continues:
                        start = self._last_frame(clips[-1], scratch / f"shot-{shot.index + 1}-start.png")
                    request = self._shot_request(model, shot, start, base_params, user_ref)
                    outcome = runner.run_blocking(
                        request, on_progress=self._shot_reporter(shot, generation_outputs, shot_state), is_cancelled=is_cancelled,
                    )
                    generation_outputs(CostGenerationOutput(
                        model=request.model,
                        amount_usd=outcome.cost.amount_usd if outcome.cost else None,
                        source=outcome.cost.source if outcome.cost else "provider",
                        task=request.task,
                        count=1,
                        params=dict(request.params),
                        outputs=len(outcome.artifacts),
                    ))
                    clip = self._shot_clip(outcome)
                except CloudRunCancelled:
                    shot_state(shot, "cancelled")
                    skip_after(shot)
                    return PipeOutput(output={"image": [], "video": [], "audio": [], "seed": []})
                except CloudRunError as error:
                    shot_state(shot, "failed", message=error.user_message)
                    skip_after(shot)
                    raise self._shot_failure(error, shot, len(clips)) from None
                except BaseException:
                    shot_state(shot, "failed", message="Something went wrong while making this shot.")
                    skip_after(shot)
                    raise
                seed = outcome.seed_used if outcome.seed_used is not None else request.seed
                clips.append(clip)
                if seed is not None:
                    seeds.append(seed)
                if film:
                    saved = VideoGenerationOutput(video_path=clip, temporary=False, seed=seed)
                    generation_outputs(GalleryGenerationOutput(images=[], videos=[saved]))
                    shot_state(shot, "done", progress=1.0, video=saved)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

        videos = [] if film else list(clips)
        if film and stitch_wanted(document):
            joined = self._stitch(clips, generation_outputs)
            if joined is not None:
                videos.append(joined)
        generation_outputs(ParamGenerationOutput(name="segment_seed", values=seeds))
        return PipeOutput(output={"image": [], "video": videos, "audio": [], "seed": seeds[:1]})

    @staticmethod
    def _shot_request(
        model: str, shot: CloudShot, start: Optional[Path], base_params: Dict[str, Any], user_ref: str,
    ) -> CloudRunRequest:
        params = dict(base_params)
        if shot.duration_s is not None:
            params[SHOT_PARAM] = shot.duration_s
        inputs: Dict[str, List[Path]] = {}
        if start is not None:
            inputs["first_frame"] = [start]
        if shot.end_image is not None:
            inputs["last_frame"] = [shot.end_image]
        return CloudRunRequest(
            task=shot.task,
            model=model,
            prompt=shot.prompt,
            negative_prompt=shot.negative_prompt or None,
            seed=shot.seed,
            count=1,
            params=params,
            inputs=inputs,
            user_ref=user_ref,
        )

    @staticmethod
    def _announce(shot: CloudShot, generation_outputs: callable) -> None:
        if shot.count < 2:
            return
        detail = "starting from the previous shot's last frame" if shot.continues else "starting"
        generation_outputs(ProgressGenerationOutput(
            state=f"{shot.label}: {detail}",
            icon=Icon("film", "pulse"),
            progress=Progress(current=int(shot.index * 100 / shot.count), max=100),
            segment_id=shot.segment_id,
        ))

    @staticmethod
    def _shot_reporter(shot: CloudShot, generation_outputs: callable, shot_state: callable) -> callable:
        def report(progress: CloudRunProgress) -> None:
            text = _progress_text(progress)
            fraction = progress.fraction
            overall = (shot.index + fraction) / shot.count if fraction is not None else None
            generation_outputs(ProgressGenerationOutput(
                state=f"{shot.label}: {text}" if shot.count > 1 else text,
                icon=Icon("play", "beat"),
                progress=Progress(current=int(overall * 100), max=100) if overall is not None else None,
                segment_id=shot.segment_id,
            ))
            shot_state(shot, "generating", progress=fraction)

        return report

    @staticmethod
    def _last_frame(clip: str, dest: Path) -> Path:
        try:
            extract_frame(clip, -1).save(dest, format="PNG")
        except Exception as error:
            raise CloudRunError(
                "failed",
                "The previous shot's last frame could not be read.",
                detail=f"{type(error).__name__}: {error}",
            ) from None
        return dest

    @staticmethod
    def _shot_failure(error: CloudRunError, shot: CloudShot, finished: int) -> CloudRunError:
        if shot.count < 2:
            return error
        return CloudRunError(
            error.kind,
            error.user_message,
            detail=error.detail,
            retry_after_s=error.retry_after_s,
            context=f"{shot.label} failed. {kept_text(finished)}",
        )

    @staticmethod
    def _shot_clip(outcome: CloudRunOutcome) -> str:
        videos = sorted((item for item in outcome.artifacts if item.modality == "video"), key=lambda item: item.index)
        kept = videos[0] if videos else None
        for artifact in outcome.artifacts:
            if artifact is not kept:
                artifact.path.unlink(missing_ok=True)
        if kept is None:
            raise CloudRunError("failed", "The provider returned no video.")
        return str(kept.path)

    @staticmethod
    def _stitch(clips: List[str], generation_outputs: callable) -> Optional[str]:
        generation_outputs(ProgressGenerationOutput(state="Joining the shots", icon=Icon("film", "pulse")))
        with tempfile.NamedTemporaryFile(prefix="potionui-cloud-film-", suffix=".mp4", delete=False) as handle:
            out_path = Path(handle.name)
        try:
            stitch_clips(clips, out_path)
        except (StitchError, OSError) as error:
            out_path.unlink(missing_ok=True)
            logger.warning(f"[CLOUD_DIRECTOR] the shots could not be joined: {type(error).__name__}: {error}")
            generation_outputs(ProgressGenerationOutput(
                state="The shots could not be joined into one video, so each shot is kept on its own",
                icon=Icon("alert-triangle", "beat"),
            ))
            return None
        return str(out_path)

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
