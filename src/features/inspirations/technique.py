"""Derives an inspiration's `technique` label at publish time.

A pure classifier over three signals already resolved by the caller
(`src.features.inspirations.operations.publish`): the generation's `mode` name, its preset's
output `category` ("image"/"video"/"audio"/"utility"), and whether an
image/video field was among the fields submitted with a real value. None of
these three ever needs a database round-trip here, which is what keeps this
independently unit-testable.
"""

from typing import Optional

# Real preset mode names are open (any string a preset.yml declares under
# `modes:`), not the closed GenerationMode enum - "upscale"/"video_upscale"
# are both real mode directory names in the shipped preset tree. A substring
# match on the mode name is checked before the category/media-input signals
# below, since an upscale mode's category is still "image"/"video" and would
# otherwise resolve to img2img/vid2vid.
_UPSCALE_MODE_MARKER = "upscale"


def derive_technique(
    mode: Optional[str],
    category: Optional[str],
    has_image_input: bool,
    has_video_input: bool,
) -> str:
    """One of 'txt2img' | 'img2img' | 'txt2vid' | 'img2vid' | 'vid2vid' | 'upscale'.

    `has_image_input`/`has_video_input` mean an `image`/`media`-typed or
    `video`-typed form field (respectively) was submitted with a truthy
    value - independent of whether that field ended up in the allowlisted
    snapshot, since a field can be a real input signal and still be omitted
    from the public form_data.
    """
    mode_name = (mode or "").lower()
    if _UPSCALE_MODE_MARKER in mode_name:
        return "upscale"

    if category == "video":
        if has_video_input:
            return "vid2vid"
        if has_image_input:
            return "img2vid"
        return "txt2vid"

    if category == "image":
        return "img2img" if (has_image_input or has_video_input) else "txt2img"

    # category is "audio"/"utility"/unknown - none of those have a slot in
    # this vocabulary, so fall back to the generic default the input signal
    # points at.
    return "txt2vid" if has_video_input else "txt2img"
