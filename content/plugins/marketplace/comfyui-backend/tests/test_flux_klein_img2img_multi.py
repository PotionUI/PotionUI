"""Tests for the FLUX Klein 9B img2img multi-image workflow (one graph shared
by both speed profiles, in the comfyui-backend plugin's FluxKlein9b preset).

Validates that the second (optional) reference image chain is wired correctly
when the image is provided, and is cleanly removed (with the CFGGuider rewired
back to the image-1 reference latents) when it is not.

The Jinja `condition` strings in pipeline.yml are rendered to literal
"true"/"false" by the preset template engine BEFORE the ComfyUI pipe runs, so
here we exercise apply_node_manipulations with pre-rendered conditions, mirroring
what the pipe actually receives at runtime.
"""

import json
import sys
import unittest
from pathlib import Path

# Add plugin path to allow importing the ComfyUI pipe from the plugin
PLUGIN_PATH = Path(__file__).resolve().parents[5] / "content" / "plugins" / "marketplace" / "comfyui-backend"
sys.path.insert(0, str(PLUGIN_PATH))

from backend.pipes.comfyui.main import ComfyUIPipe  # noqa: E402
from unittest.mock import Mock  # noqa: E402
from src.plugin_api.pipes import PipeInput  # noqa: E402

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[5]
    / "content" / "plugins" / "marketplace" / "comfyui-backend" / "presets" / "FluxKlein9b"
    / "modes" / "img2img" / "files" / "workflows" / "img2img.json"
)

# Image-2 chain node ids (must match img2img.json / pipeline.yml)
IMAGE2_NODES = ["81", "85", "86", "87", "88"]


def image2_manipulations(provided: bool):
    """Build the node_manipulations list exactly as pipeline.yml produces it
    once the Jinja conditions have been rendered.

    When image 2 is provided -> conditions render "false" (keep everything).
    When image 2 is absent    -> conditions render "true" (remove the chain).
    """
    cond = "false" if provided else "true"
    return [
        {"type": "remove_node", "condition": cond, "node_id": "87"},
        {"type": "remove_node", "condition": cond, "node_id": "88"},
        {"type": "remove_node", "condition": cond, "node_id": "86"},
        {"type": "remove_node", "condition": cond, "node_id": "85"},
        {"type": "remove_node", "condition": cond, "node_id": "81"},
        {"type": "update_node_input", "condition": cond, "node_id": "75:63",
         "input_key": "positive", "input_value": ["75:79:77", 0]},
        {"type": "update_node_input", "condition": cond, "node_id": "75:63",
         "input_key": "negative", "input_value": ["75:79:76", 0]},
    ]


class TestFluxKleinImg2ImgMulti(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.workflow = json.loads(WORKFLOW_PATH.read_text())
        self.pipe = ComfyUIPipe({
            "host": "127.0.0.1",
            "port": 8188,
            "workflow_file": str(WORKFLOW_PATH),
            "timeout": 30,
            "secure": False,
        })

    def test_baked_workflow_wires_both_images(self):
        """The on-disk workflow chains image-2 refs into the CFGGuider."""
        wf = self.workflow
        # Both LoadImage nodes exist
        self.assertEqual(wf["76"]["class_type"], "LoadImage")
        self.assertEqual(wf["81"]["class_type"], "LoadImage")
        # Image-2 reference latents wrap the image-1 reference latents
        self.assertEqual(wf["87"]["inputs"]["conditioning"], ["75:79:77", 0])
        self.assertEqual(wf["87"]["inputs"]["latent"], ["86", 0])
        self.assertEqual(wf["88"]["inputs"]["conditioning"], ["75:79:76", 0])
        self.assertEqual(wf["88"]["inputs"]["latent"], ["86", 0])
        # Image-2 VAEEncode reads the image-2 scale node
        self.assertEqual(wf["86"]["inputs"]["pixels"], ["85", 0])
        self.assertEqual(wf["85"]["inputs"]["image"], ["81", 0])
        # CFGGuider points at the image-2 refs by default
        self.assertEqual(wf["75:63"]["inputs"]["positive"], ["87", 0])
        self.assertEqual(wf["75:63"]["inputs"]["negative"], ["88", 0])
        # Output size is driven by image 1 only
        self.assertEqual(wf["75:81"]["inputs"]["image"], ["75:80", 0])
        self.assertEqual(wf["75:80"]["inputs"]["image"], ["76", 0])

    async def test_image2_provided_keeps_chain(self):
        """With image 2 provided, no nodes are removed and guider keeps img2 refs."""
        self.pipe.config["node_manipulations"] = image2_manipulations(provided=True)
        result = await self.pipe.apply_node_manipulations(self.workflow, PipeInput(input={}), Mock())

        for node_id in IMAGE2_NODES:
            self.assertIn(node_id, result, f"node {node_id} should be kept")
        self.assertEqual(result["75:63"]["inputs"]["positive"], ["87", 0])
        self.assertEqual(result["75:63"]["inputs"]["negative"], ["88", 0])

    async def test_image2_absent_removes_chain_and_rewires(self):
        """Without image 2, the chain is removed and guider falls back to img1 refs."""
        self.pipe.config["node_manipulations"] = image2_manipulations(provided=False)
        result = await self.pipe.apply_node_manipulations(self.workflow, PipeInput(input={}), Mock())

        # Image-2 chain fully removed
        for node_id in IMAGE2_NODES:
            self.assertNotIn(node_id, result, f"node {node_id} should be removed")

        # Guider rewired back to image-1 reference latents
        self.assertEqual(result["75:63"]["inputs"]["positive"], ["75:79:77", 0])
        self.assertEqual(result["75:63"]["inputs"]["negative"], ["75:79:76", 0])

        # Image-1 chain is intact and still feeds the sampler
        for node_id in ["76", "75:80", "75:81", "75:79:78", "75:79:77", "75:79:76", "75:63"]:
            self.assertIn(node_id, result)

        # No dangling references to removed nodes anywhere in the graph
        for node_id, node in result.items():
            for key, val in node.get("inputs", {}).items():
                if isinstance(val, list) and len(val) == 2:
                    self.assertNotIn(
                        str(val[0]), IMAGE2_NODES,
                        f"node {node_id}.{key} still references removed node {val[0]}",
                    )


if __name__ == "__main__":
    unittest.main()
