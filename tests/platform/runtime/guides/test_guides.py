import numpy as np
import pytest

from src.platform.runtime.guides.edges import canny_guide
from src.platform.runtime.guides.frames import fit_frame


def square_on_black(size=64):
    frame = np.zeros((size, size, 3), dtype=np.uint8)
    frame[20:44, 20:44] = 255
    return frame


class TestCanny:
    def test_a_white_square_gives_a_white_outline_on_black(self):
        guide = canny_guide(square_on_black())
        assert guide.shape == (64, 64, 3) and guide.dtype == np.uint8
        assert set(np.unique(guide)) == {0, 255}
        assert guide[32, 32, 0] == 0 and guide[0, 0, 0] == 0
        assert guide[18:22, 30:34, 0].max() == 255

    def test_the_three_channels_are_identical(self):
        guide = canny_guide(square_on_black())
        assert np.array_equal(guide[:, :, 0], guide[:, :, 1]) and np.array_equal(guide[:, :, 1], guide[:, :, 2])

    def test_a_flat_frame_has_no_edges(self):
        assert canny_guide(np.full((32, 32, 3), 90, dtype=np.uint8)).max() == 0

    def test_higher_thresholds_drop_the_weak_edges(self):
        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        frame[:, 32:] = 40
        assert canny_guide(frame, 10, 30).max() == 255
        assert canny_guide(frame, 200, 400).max() == 0


class TestFitFrame:
    def frame(self, width, height):
        return np.full((height, width, 3), 200, dtype=np.uint8)

    def test_the_same_size_is_returned_untouched(self):
        frame = self.frame(40, 30)
        assert fit_frame(frame, 40, 30) is frame

    def test_contain_letterboxes_and_keeps_the_whole_frame(self):
        out = fit_frame(self.frame(80, 40), 64, 64, "contain")
        assert out.shape == (64, 64, 3)
        assert out[0, 32].max() == 0 and out[63, 32].max() == 0
        assert out[32, 32, 0] == 200

    def test_cover_fills_the_canvas_and_crops(self):
        out = fit_frame(self.frame(80, 40), 64, 64, "cover")
        assert out.shape == (64, 64, 3)
        assert out.min() == 200

    def test_a_grey_channel_frame_keeps_its_rank(self):
        out = fit_frame(np.full((40, 80), 9, dtype=np.uint8), 64, 64, "contain")
        assert out.shape == (64, 64)

    def test_an_unknown_mode_is_rejected(self):
        with pytest.raises(ValueError, match="fit"):
            fit_frame(self.frame(80, 40), 64, 64, "stretch")
