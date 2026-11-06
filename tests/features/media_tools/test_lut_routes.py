import shutil
from pathlib import Path

import numpy as np

from src.platform.media_tools.effects.loader import core_effects
from src.platform.media_tools.effects.runtime import EffectRuntime
from src.platform.media_tools.effects.tool import EffectTool
from tests.features.media_tools.test_effect_routes import EffectRoutesTestBase

FIXTURES = Path(next(effect for effect in core_effects() if effect.id == "lut").folder) / "parity"
TABLE_URL = "/api/media-tools/luts/table"


class TestLutRoutes(EffectRoutesTestBase):

    def setUp(self):
        super().setUp()
        self.luts = Path(self.temp_dir) / "luts"
        shutil.copytree(FIXTURES, self.luts / "grades")
        runtime = EffectRuntime()
        runtime.use_asset_dirs(lambda kind: [self.luts] if kind == "lut" else [])
        for spec in self.registry.list():
            if isinstance(spec.impl, EffectTool):
                spec.impl.runtime = runtime

    def test_the_catalog_lists_the_installed_luts_as_choices(self):
        tool = self.catalog(kind="video")["lut"]

        param = next(param for param in tool["params"] if param["name"] == "lut")
        self.assertEqual(param["type"], "lut")
        self.assertEqual(
            [option["value"] for option in param["options"]],
            ["", "grades/cool.cube", "grades/shaped.cube", "grades/warm.cube"],
        )
        self.assertEqual(param["options"][0]["label"], "None")
        self.assertEqual(tool["effect"]["textures"], [{"uniform": "table", "from": "lut", "kind": "lut3d"}])

    def test_the_table_is_the_baked_lut_as_16_bit_rgb(self):
        response = self.client.get(TABLE_URL, params={"ref": "grades/shaped.cube"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/octet-stream")
        table = np.frombuffer(response.content, dtype="<u2")
        self.assertEqual(table.size, 33 ** 3 * 3)
        self.assertTrue(np.allclose(table[-3:], [65535, 0.98 * 65535, 65535], atol=40))

    def test_unknown_or_escaping_refs_are_not_found(self):
        for ref in ("grades/missing.cube", "../secret.cube", "/etc/passwd", ""):
            response = self.client.get(TABLE_URL, params={"ref": ref})
            self.assertEqual(response.status_code, 404, ref)

    def test_the_table_needs_a_signed_in_user(self):
        response = self.anonymous().get(TABLE_URL, params={"ref": "grades/warm.cube"})

        self.assertIn(response.status_code, (401, 403))
