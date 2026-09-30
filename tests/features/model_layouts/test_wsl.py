import pytest

from src.features.model_layouts.wsl import PathTranslator


def _posix(mounted=("c", "d"), mount_root="/wsl"):
    return PathTranslator(
        is_windows=False,
        mount_root=mount_root,
        is_dir=lambda p: p in {f"{mount_root}/{letter}" for letter in mounted},
    )


def _windows():
    return PathTranslator(is_windows=True, is_dir=lambda p: False)


@pytest.mark.parametrize("raw,expected", [
    ("C:\\Users\\me\\models", "/wsl/c/Users/me/models"),
    ("D:/Fooocus/models/checkpoints", "/wsl/d/Fooocus/models/checkpoints"),
    ("c:\\", "/wsl/c"),
    ("D:", "/wsl/d"),
    ("C:\\a\\b\\", "/wsl/c/a/b"),
    ("  C:\\padded  ", "/wsl/c/padded"),
])
def test_windows_drive_paths_become_mnt_paths(raw, expected):
    result = _posix().translate(raw)
    assert result.path == expected and result.warning is None


def test_missing_mount_warns_and_returns_nothing():
    result = _posix().translate("E:\\models")
    assert result.path is None
    assert "/wsl/e is not mounted" in result.warning


def test_custom_mount_root():
    translator = _posix(mounted=("c",), mount_root="/drives")
    assert translator.translate("C:\\x").path == "/drives/c/x"


def test_posix_paths_pass_through_on_posix():
    assert _posix().translate("/srv/models").path == "/srv/models"
    assert _posix().translate("relative/dir").path == "relative/dir"


def test_backslash_relative_path_is_converted_on_posix():
    assert _posix().translate("models\\checkpoints").path == "models/checkpoints"


def test_backslash_kept_when_path_already_has_slashes():
    assert _posix().translate("a/b\\c").path == "a/b\\c"


def test_unc_path_warns_on_posix():
    result = _posix().translate("\\\\server\\share\\models")
    assert result.path is None and "network path" in result.warning


def test_windows_host_keeps_windows_paths_untouched():
    assert _windows().translate("C:\\models").path == "C:\\models"
    assert _windows().translate("\\\\server\\share").path == "\\\\server\\share"


def test_windows_host_warns_on_posix_absolute_path():
    result = _windows().translate("/wsl/c/models")
    assert result.path is None and "POSIX path" in result.warning


@pytest.mark.parametrize("raw", [
    "$HOME/models", "${MODELS}/x", "%USERPROFILE%\\models", "C:\\%APPDATA%\\x",
    "%~dp0models", "%~dp0\\models", "$(pwd)/models", "`pwd`/models", "%0\\..", "$1/x", "$$/x",
])
def test_environment_variables_are_skipped_with_warning(raw):
    for translator in (_posix(), _windows()):
        result = translator.translate(raw)
        assert result.path is None and "environment variable" in result.warning


def test_tilde_uses_the_injected_home():
    translator = PathTranslator(is_windows=False, expand_user=lambda p: p.replace("~", "/home/user", 1))
    assert translator.translate("~/models").path == "/home/user/models"
    assert translator.translate("~").path == "/home/user"


def test_tilde_inside_a_name_is_left_alone():
    translator = PathTranslator(is_windows=False, expand_user=lambda p: "BOOM")
    assert translator.translate("/srv/a~b").path == "/srv/a~b"


def test_empty_value_yields_nothing_quietly():
    result = _posix().translate("   ")
    assert result.path is None and result.warning is None


def test_flavor_follows_the_host_kind():
    import ntpath
    import posixpath

    assert PathTranslator(is_windows=True).flavor is ntpath
    assert PathTranslator(is_windows=False).flavor is posixpath


def test_default_mount_root_is_the_wsl_automount_default():
    assert PathTranslator().mount_root == "/mnt"
