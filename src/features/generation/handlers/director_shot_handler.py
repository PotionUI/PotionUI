from typing import Any, Dict, Optional

from src.features.generation.output_types import OutputTypeSpec, SerializeContext, output_type_registry
from src.pipelines.outputs import DirectorShotGenerationOutput


def _saved_clip(output: DirectorShotGenerationOutput) -> Optional[str]:
    video = output.video
    if video is None or getattr(video, "temporary", True) or getattr(video, "_preview_suppressed", False):
        return None
    saved = getattr(video, "_saved_path", None)
    return str(saved).replace("\\", "/") if saved else None


def serialize_director_shot_output(output: DirectorShotGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    saved = _saved_clip(output)
    video = output.video
    return {
        "shot_id": output.shot_id,
        "status": output.status,
        "shot_index": output.shot_index,
        "shot_count": output.shot_count,
        "progress": output.progress,
        "message": output.message,
        "output_url": f"/api/media/generations/{ctx.generation_id}/{saved.rsplit('/', 1)[-1]}" if saved else None,
        "output_path": saved,
        "nsfw": bool(getattr(video, "_content_nsfw", False)) if video is not None else False,
    }


output_type_registry.register(OutputTypeSpec(
    output_cls=DirectorShotGenerationOutput,
    key="director_shot",
    message_type="director_shot_update",
    serializer=serialize_director_shot_output,
    handler_cls=None,
))
