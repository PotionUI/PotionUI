import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.media_tools.routes import MediaToolController, build_router
from src.platform.media_tools.ops import register_core_tools
from tests.features.media_tools.support import MediaToolsTestBase, needs_ffmpeg

FIX = {"tool": "fix_colors", "params": {"strength": 80}}
TRIM = {"tool": "trim", "params": {"start": 0.2, "end": 0.8}}
SHADER_URL = "/api/media-tools/effects/{}/shader"
ANALYSIS_URL = "/api/media-tools/effects/analysis"


class EffectRoutesTestBase(MediaToolsTestBase):

    def setUp(self):
        super().setUp()
        register_core_tools(self.registry)

    def catalog(self, **query):
        response = self.client.get("/api/media-tools", params=query)
        self.assertEqual(response.status_code, 200, response.text)
        return {tool["id"]: tool for tool in response.json()["data"]["tools"]}

    def anonymous(self):
        class Container:
            media_tool_controller = MediaToolController(self.service)

        app = FastAPI()
        app.include_router(build_router(Container()))
        return TestClient(app)

    def analyse(self, source, steps, step_index):
        return self.client.post(ANALYSIS_URL, json={"input": source, "steps": steps, "step_index": step_index})


class TestEffectCatalog(EffectRoutesTestBase):

    def test_effects_carry_the_effect_block_and_other_tools_carry_null(self):
        tools = self.catalog(kind="video")

        grain = tools["film_grain"]["effect"]
        self.assertEqual(grain["group"], "texture")
        self.assertEqual(grain["time"], "frame")
        self.assertEqual(grain["shader"]["url"], SHADER_URL.format("film_grain"))
        self.assertRegex(grain["shader"]["etag"], r'^"[0-9a-f]{16}"$')
        self.assertEqual(
            [(row["name"], row["type"], row["from"], row["key"]) for row in grain["uniforms"]],
            [
                ("u_amount", "float", "param", "amount"),
                ("u_size", "float", "param", "size"),
                ("u_color", "bool", "param", "color"),
                ("u_seed", "int", "param", "seed"),
                ("u_strength", "float", "derive", "strength"),
                ("u_cell", "float", "derive", "cell"),
            ],
        )
        self.assertEqual(grain["derive"][0], {"name": "strength", "expr": "amount / 100 * 0.14 * 255"})
        self.assertEqual((grain["textures"], grain["gain"], grain["analysis"]), ([], None, False))
        self.assertEqual(grain["analysis_keys"], [])
        self.assertTrue(tools["fix_colors"]["effect"]["analysis"])
        self.assertEqual(tools["fix_colors"]["effect"]["analysis_keys"], ["white_balance", "contrast"])
        self.assertEqual(tools["fix_colors"]["effect"]["group"], "colour")
        self.assertEqual(tools["fix_colors"]["group"], "effects")
        self.assertIsNone(tools["trim"]["effect"])
        self.assertIsNone(tools["rot"]["effect"])


class TestEffectShader(EffectRoutesTestBase):

    def test_the_shader_text_is_served_as_plain_text_with_its_etag(self):
        etag = self.catalog()["film_grain"]["effect"]["shader"]["etag"]

        response = self.client.get(SHADER_URL.format("film_grain"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["etag"], etag)
        self.assertIn("vec4 effect(vec4 color, vec2 uv)", response.text)
        self.assertNotIn("#version", response.text)

    def test_a_matching_etag_gets_not_modified(self):
        etag = self.client.get(SHADER_URL.format("film_grain")).headers["etag"]

        response = self.client.get(SHADER_URL.format("film_grain"), headers={"If-None-Match": f'"other", {etag}'})

        self.assertEqual(response.status_code, 304)
        self.assertEqual(response.content, b"")

    def test_unknown_tools_and_tools_that_are_not_effects_are_not_found(self):
        for tool_id in ("nope", "trim", "rot", "..%2Fsecret", "acme.mirror"):
            response = self.client.get(SHADER_URL.format(tool_id))
            self.assertEqual(response.status_code, 404, tool_id)

    def test_a_session_is_required(self):
        response = self.anonymous().get(SHADER_URL.format("film_grain"))

        self.assertEqual(response.status_code, 401)


class TestEffectAnalysisRefusals(EffectRoutesTestBase):

    def test_a_session_is_required(self):
        response = self.anonymous().post(ANALYSIS_URL, json={"input": {"item_id": "x"}, "steps": [FIX], "step_index": 0})

        self.assertEqual(response.status_code, 401)

    def test_someone_elses_or_a_missing_item_is_not_found(self):
        item = self.image_item(user_id=self.other_user_id)

        for source in ({"item_id": item.id}, {"item_id": "missing"}):
            response = self.analyse(source, [FIX], 0)
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(self.error_of(response)["error"], "not_found")

    def test_the_step_must_be_a_visible_effect_with_an_analysis(self):
        item = self.image_item()
        hidden = {**FIX, "hidden": True}

        for steps, index in (([FIX], 1), ([FIX], -1), ([hidden], 0), ([TRIM], 0), ([{"tool": "film_grain"}], 0)):
            response = self.analyse({"item_id": item.id}, steps, index)
            self.assertIn(response.status_code, (400, 422), response.text)
            if response.status_code == 400:
                self.assertEqual(self.error_of(response)["error"], "invalid_params")

    def test_unknown_tools_and_bad_params_are_refused_before_measuring(self):
        item = self.image_item()

        unknown = self.analyse({"item_id": item.id}, [{"tool": "nope"}, FIX], 1)
        bad = self.analyse({"item_id": item.id}, [{"tool": "fix_colors", "params": {"strength": 500}}], 0)

        self.assertEqual(self.error_of(unknown)["error"], "unknown_tool")
        self.assertEqual(self.error_of(bad)["error"], "invalid_params")

    def test_an_image_cannot_be_measured(self):
        item = self.image_item()

        response = self.analyse({"item_id": item.id}, [FIX], 0)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error_of(response)["error"], "unsupported_media")


@needs_ffmpeg
class TestEffectAnalysis(EffectRoutesTestBase):

    def test_values_are_measured_then_served_from_the_cache(self):
        clip = self.video_item(seconds=1.0)

        first = self.analyse({"item_id": clip.id}, [TRIM, FIX], 1)
        again = self.analyse({"item_id": clip.id}, [TRIM, {**FIX, "params": {"strength": 20}}], 1)
        other_window = self.analyse({"item_id": clip.id}, [FIX], 0)

        self.assertEqual(first.status_code, 200, first.text)
        values = first.json()["data"]["values"]
        self.assertEqual(set(values), {"gain_r", "gain_g", "gain_b", "black", "white"})
        self.assertFalse(first.json()["data"]["cached"])
        self.assertTrue(again.json()["data"]["cached"])
        self.assertEqual(again.json()["data"]["values"], values)
        self.assertFalse(other_window.json()["data"]["cached"])

    def test_hidden_steps_before_the_effect_are_skipped(self):
        clip = self.video_item(seconds=1.0)

        hidden_trim = self.analyse({"item_id": clip.id}, [{**TRIM, "hidden": True}, FIX], 1)
        no_trim = self.analyse({"item_id": clip.id}, [FIX], 0)

        self.assertEqual(hidden_trim.status_code, 200, hidden_trim.text)
        self.assertTrue(no_trim.json()["data"]["cached"])


if __name__ == "__main__":
    unittest.main()
