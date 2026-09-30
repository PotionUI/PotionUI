"""Working with images.

`convert_image_to_base64` encodes a PIL image for transport - to the frontend, or
to a vision model that takes images inline.

`BackgroundMattingModel` runs BiRefNet background matting (an RGB(A) image in,
RGBA out at the same size, alpha = the matted subject). Load it, use it,
release it - it is never resident:

    model = BackgroundMattingModel.from_checkpoint(checkpoint_path)  # raises
                                                                       # ValueError
                                                                       # if the
                                                                       # file is
                                                                       # missing
                                                                       # or the
                                                                       # wrong
                                                                       # shape
    try:
        model.to("cuda")
        matted = model(image)
    finally:
        model.cpu()
        del model

`checkpoint_path` is a `.safetensors` file the plugin/user supplies (e.g. from
the model depot's `detection_segm/` folder, the convention
`content/plugins/marketplace/trellis2` established) - nothing is ever downloaded.
"""

from typing import TYPE_CHECKING

from src.platform.util.imaging import convert_image_to_base64

if TYPE_CHECKING:
    from src.platform.runtime.native.matting import BackgroundMattingModel

__all__ = [
    "BackgroundMattingModel",
    "convert_image_to_base64",
]


def __getattr__(name):
    if name == "BackgroundMattingModel":
        from src.platform.runtime.native.matting import BackgroundMattingModel
        return BackgroundMattingModel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
