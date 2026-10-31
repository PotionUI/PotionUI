import base64
from typing import Any, Dict

from src.features.generation.preview_clip_message import ADMIN_DETAIL_KEY, PREVIEW_CLIP_MESSAGE
from src.features.generation.output_types import OutputTypeSpec, SerializeContext, output_type_registry
from src.pipelines.outputs import PreviewClipGenerationOutput


def serialize_preview_clip_output(output: PreviewClipGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    encoded = base64.b64encode(output.encoded).decode("ascii")
    return {
        "temporary": True,
        "image": f"data:image/webp;base64,{encoded}",
        "frames": output.frames,
        "columns": output.columns,
        "frame_width": output.frame_width,
        "frame_height": output.frame_height,
        "fps": output.fps,
        "step": output.step,
        "total_steps": output.total_steps,
        "source_indices": list(output.source_indices),
        ADMIN_DETAIL_KEY: {
            "format": "WEBP",
            "bytes": len(output.encoded),
            "long_edge": output.long_edge,
            "update": output.update_index,
            "updates_max": output.updates_max,
            "every": output.every,
        },
    }


output_type_registry.register(OutputTypeSpec(
    output_cls=PreviewClipGenerationOutput,
    key="preview_clip",
    message_type=PREVIEW_CLIP_MESSAGE,
    serializer=serialize_preview_clip_output,
    handler_cls=None,
))
