import pytest

from src.pipelines.contracts import PIPE_FAMILY_TITLES, resolve_display_title


class TestResolveDisplayTitle:
    def test_override_wins_regardless_of_name(self):
        assert resolve_display_title("generator", "Custom step name") == "Custom step name"

    @pytest.mark.parametrize(
        "pipe_name,expected",
        [
            ("checkpoint_loader", "Loading model"),
            ("model_loader", "Loading model"),
            ("generator", "Generating"),
            ("upscaler", "Upscaling"),
        ],
    )
    def test_known_family_fallback(self, pipe_name, expected):
        assert resolve_display_title(pipe_name) == expected

    def test_variant_suffix_is_stripped_before_family_lookup(self):
        assert resolve_display_title("interpolator/rife") == "Interpolating frames"

    def test_unknown_pipe_name_is_cleaned_up(self):
        assert resolve_display_title("my_custom_plugin_step") == "My custom plugin step"

    def test_missing_name_falls_back_to_processing(self):
        assert resolve_display_title(None) == "Processing"

    def test_every_family_title_is_non_empty(self):
        assert all(title for title in PIPE_FAMILY_TITLES.values())
