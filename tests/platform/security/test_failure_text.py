import pytest

from src.platform.security.failure_text import GENERIC_FAILURE, basename_paths, failure_detail, hide_endpoints, scrub_paths


def test_scrub_paths_hides_absolute_paths_only():
    text = scrub_paths("missing /home/u/models/x.bin and C:\\Users\\me\\m.bin, see generations/2026/x.png")
    assert "/home/" not in text and "C:\\" not in text
    assert "generations/2026/x.png" in text


def test_regular_users_get_the_generic_message():
    assert failure_detail(RuntimeError("secret /srv/x"), False) == GENERIC_FAILURE


def test_a_caller_supplied_plain_message_replaces_the_generic_one():
    assert failure_detail(RuntimeError("secret"), False, "Could not save.") == "Could not save."


def test_admins_get_the_scrubbed_detail():
    assert failure_detail(RuntimeError("cannot open /srv/app/db"), True) == "cannot open <server path>"


def test_basename_paths_keeps_only_the_file_name_of_an_absolute_path():
    text = basename_paths(
        "cannot identify image file '/srv/potion/storage/uploads/cat.png' and C:\\Users\\me\\m.bin"
    )
    assert text == "cannot identify image file 'cat.png' and m.bin"


def test_basename_paths_leaves_relative_paths_urls_and_fractions_alone():
    text = "step 1/20 from https://example.com/org/repo and checkpoints/sdxl/x.safetensors"
    assert basename_paths(text) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Connecting to ComfyUI at gpu-box.lan:8188", "Connecting to ComfyUI\u2026"),
        ("Connecting to ComfyUI at 10.0.0.5:8188", "Connecting to ComfyUI\u2026"),
        ("Connecting to ComfyUI at [::1]:8188", "Connecting to ComfyUI\u2026"),
        ("Connecting to ComfyUI at localhost:8188", "Connecting to ComfyUI\u2026"),
        ("Fetching http://comfyui:8188/view?x=1", "Fetching \u2026"),
        ("Fetching http://10.0.0.5/x", "Fetching \u2026"),
        ("Fetching https://api.example.com:8443/v1", "Fetching \u2026"),
    ],
)
def test_hide_endpoints_hides_server_addresses(text, expected):
    assert hide_endpoints(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Rendering frames:25 at 24fps",
        "size:1024",
        "ETA:120",
        "steps:20 fps:30",
        "ratio 16:9",
        "at 12:30",
        "Step <<NUMBER:12/20>> <<PIPE:generator:bolt>>",
        "Downloading https://huggingface.co/a/b",
    ],
)
def test_hide_endpoints_leaves_ordinary_progress_text_alone(text):
    assert hide_endpoints(text) == text
