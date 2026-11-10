import hashlib
from types import SimpleNamespace

import pytest

from src.platform.util import face_models
from src.plugin_api import media

DETECTOR = b"yunet weights"
IDENTITY = b"auraface weights"


@pytest.fixture
def known(monkeypatch):
    monkeypatch.setattr(
        face_models,
        "MODEL_FILES",
        {
            face_models.FACE_MODEL: (len(DETECTOR), hashlib.sha256(DETECTOR).hexdigest()),
            face_models.IDENTITY_MODEL: (len(IDENTITY), hashlib.sha256(IDENTITY).hexdigest()),
        },
    )
    monkeypatch.setattr(face_models, "_verified_files", set())


class Roots:
    def __init__(self, folders, home):
        self.folders = folders
        self.home = home
        self.asked = []

    def type_dirs(self, model_type):
        self.asked.append(model_type)
        return [SimpleNamespace(path=str(folder)) for folder in self.folders]

    def asset_dir(self, subdir):
        self.asked.append(subdir)
        return self.home / subdir


def test_the_model_file_check_and_its_state_live_in_the_platform_not_in_the_plugin_api():
    for name in ("MODEL_FILES", "_verified_files", "_sha256", "_is_model_file", "FACE_MODEL", "IDENTITY_MODEL", "IDENTITY_DIR"):
        assert not hasattr(media, name)
        assert hasattr(face_models, name)


def test_the_detector_is_the_first_valid_file_of_the_detection_bbox_folders_of_the_roots_given(tmp_path, known):
    first, second, third = (tmp_path / name for name in "abc")
    for folder in (first, second, third):
        folder.mkdir()
    (first / face_models.FACE_MODEL).write_bytes(b"other weights")
    (second / face_models.FACE_MODEL).write_bytes(DETECTOR)
    (third / face_models.FACE_MODEL).write_bytes(DETECTOR)
    roots = Roots([first, second, third], tmp_path)

    assert face_models.face_model(roots) == second / face_models.FACE_MODEL
    assert roots.asked == ["detection_bbox"]
    assert face_models.face_model(Roots([first], tmp_path)) is None


def test_the_identity_model_is_its_file_in_the_auraface_folder_of_the_home_root_given(tmp_path, known):
    roots = Roots([], tmp_path)
    folder = tmp_path / "face_recognition" / "auraface"

    missing = face_models.identity_model(roots)
    folder.mkdir(parents=True)
    (folder / face_models.IDENTITY_MODEL).write_bytes(IDENTITY)

    assert missing is None
    assert face_models.identity_model(roots) == folder / face_models.IDENTITY_MODEL
    assert roots.asked == ["face_recognition/auraface", "face_recognition/auraface"]
