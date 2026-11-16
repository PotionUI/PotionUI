import pytest

from src.features.media.records import Upload
from src.features.media.validators import (
    UPLOAD_PURPOSE_EDITOR_RUN,
    UPLOAD_PURPOSES,
    validate_upload_purpose_policy,
)
from src.features.media_tools.sources import ResolvedInput


def test_a_client_facing_purpose_check_refuses_the_editor_run_purpose():
    assert UPLOAD_PURPOSE_EDITOR_RUN not in UPLOAD_PURPOSES
    with pytest.raises(ValueError):
        validate_upload_purpose_policy(UPLOAD_PURPOSE_EDITOR_RUN)
    assert validate_upload_purpose_policy(UPLOAD_PURPOSE_EDITOR_RUN, internal=True) == UPLOAD_PURPOSE_EDITOR_RUN


def test_only_a_user_upload_is_a_library_item():
    def resolved(purpose):
        upload = Upload(user_id="u1", filename="f.png", original_filename="f.png", media_type="image", mime_type="image/png", file_size=1, purpose=purpose)
        return ResolvedInput(kind="image", key="k", suffix=".png", original_filename="f.png", upload=upload)

    assert resolved("user_upload").is_library_item
    assert not resolved("derived_artifact").is_library_item
    assert not resolved(UPLOAD_PURPOSE_EDITOR_RUN).is_library_item
