"""
Validation policy for media upload requests.
"""

from typing import Optional

# 'user_upload' is a real Library resource - a direct upload or a copied
# generation file, meant to be browsed. 'derived_artifact' is a file that had
# to exist on disk for some other feature to reference by path (e.g. a
# painted inpainting mask, addressed by the `${name}_inpaint_mask` sibling
# channel) and was never meant to show up in anyone's Library.
UPLOAD_PURPOSE_USER = "user_upload"
UPLOAD_PURPOSE_DERIVED = "derived_artifact"
UPLOAD_PURPOSES = (UPLOAD_PURPOSE_USER, UPLOAD_PURPOSE_DERIVED)


def validate_upload_purpose_policy(value: str) -> str:
    if value not in UPLOAD_PURPOSES:
        raise ValueError(f"purpose must be one of {UPLOAD_PURPOSES}")
    return value

DERIVED_ARTIFACT_MAX_BYTES = 512 * 1024 * 1024
DERIVED_ARTIFACT_TYPES = ("image/", "audio/", "video/")


def validate_derived_artifact(size: Optional[int], content_type: Optional[str]) -> None:
    if content_type is not None and not content_type.lower().startswith(DERIVED_ARTIFACT_TYPES):
        raise ValueError("A helper file must be an image, audio or video")
    if isinstance(size, int) and size > DERIVED_ARTIFACT_MAX_BYTES:
        raise ValueError(f"A helper file can be at most {DERIVED_ARTIFACT_MAX_BYTES // (1024 * 1024)} MB")
