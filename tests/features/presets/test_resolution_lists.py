"""Low-tier entries in the shared image resolution lists must land on the
target family's real DiT patch granularity, verified against the same helper
the native engine snaps requests with -- not a hand-copied constant.
"""

import unittest
from pathlib import Path

import yaml

from src.platform.runtime.native.resolution import snap_resolution

RESOLUTIONS_DIR = Path(__file__).resolve().parents[3] / "content" / "presets" / "_shared" / "resolutions"


def _load(name: str) -> list[dict]:
    with open(RESOLUTIONS_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _dims(value: str) -> tuple[int, int]:
    w, h = value.split("x")
    return int(w), int(h)


class TestSharedResolutionListsHaveLowTiers(unittest.TestCase):
    """Every family's list must offer square tiers at 512, 640 and 768 so an
    8-12 GB card has a resolution that fits without the user hunting for one."""

    def _assert_has_square_tiers(self, entries: list[dict], sizes: tuple[int, ...]) -> None:
        values = {entry["value"] for entry in entries}
        for size in sizes:
            self.assertIn(
                f"{size}x{size}", values,
                f"expected a {size}x{size} tier, got {sorted(values)}",
            )

    def test_flux_has_low_tiers(self):
        self._assert_has_square_tiers(_load("flux.yml"), (512, 640, 768))

    def test_sdxl_has_low_tiers(self):
        self._assert_has_square_tiers(_load("sdxl.yml"), (512, 640, 768))

    def test_zimage_has_low_tiers(self):
        self._assert_has_square_tiers(_load("zimage.yml"), (512, 640, 768))


class TestSharedResolutionListsSnapCleanly(unittest.TestCase):
    """Every entry must already be exactly what snap_resolution() would
    produce for its family's granularity -- a value that isn't loses pixels
    silently at generation time and the picker lies about what it offers.

    Granularity per src/pipelines/pipes/_shared/generation/flow_generator_pipe.py:
    Flux1 16px / Flux2 (Klein) 32px, Krea-2/Qwen/Z-Image/Anima 16px. flux.yml is
    shared by Flux (which covers both Flux1 and Klein) and Krea-2, so it is
    snapped at 32px, the stricter of the two. sdxl.yml is shared by SDXL,
    Anima, QwenImage and ZImage; its existing entries are already 64px
    multiples (a safe superset of the 16px the DiT-family consumers need), so
    new entries stay on that same 64px grid. zimage.yml (used by the
    comfyui-backend plugin's zImage preset) stays on the family's own 16px.
    """

    def _assert_all_pre_snapped(self, entries: list[dict], granularity: int) -> None:
        for entry in entries:
            w, h = _dims(entry["value"])
            snapped_w, snapped_h = snap_resolution(w, h, spatial_downscale=granularity, patch_size=1)
            self.assertEqual(
                (w, h), (snapped_w, snapped_h),
                f"{entry['value']} is not {granularity}px-snapped (snaps to {snapped_w}x{snapped_h})",
            )

    def test_flux_low_tiers_snap_to_32px(self):
        entries = [e for e in _load("flux.yml") if _dims(e["value"])[0] <= 1024 and _dims(e["value"])[1] <= 1024]
        self._assert_all_pre_snapped(entries, granularity=32)

    def test_sdxl_low_tiers_snap_to_64px(self):
        entries = [e for e in _load("sdxl.yml") if _dims(e["value"])[0] <= 1024 and _dims(e["value"])[1] <= 1024]
        self._assert_all_pre_snapped(entries, granularity=64)

    def test_zimage_low_tiers_snap_to_16px(self):
        entries = [e for e in _load("zimage.yml") if "Minimum" in e["description"] or "Small" in e["description"] or "Compact" in e["description"]]
        self.assertTrue(entries, "expected Minimum/Small/Compact tier entries in zimage.yml")
        self._assert_all_pre_snapped(entries, granularity=16)


if __name__ == "__main__":
    unittest.main()
