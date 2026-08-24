"""
Validation policy for media upload requests.
"""

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
