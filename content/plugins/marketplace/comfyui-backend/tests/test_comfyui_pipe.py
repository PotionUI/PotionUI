import unittest
import asyncio
import json
import sys
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from pathlib import Path
from PIL import Image
import io

import pytest

# Add plugin path to allow importing the ComfyUI pipe from the plugin
plugin_path = Path(__file__).resolve().parents[5] / "content" / "plugins" / "marketplace" / "comfyui-backend"
sys.path.insert(0, str(plugin_path))

from backend.pipes.comfyui.main import ComfyUIPipe
from src.plugin_api.pipes import (
    PipeInput,
    IOType,
    ImageGenerationOutput,
    ProgressGenerationOutput,
    GenerationExecutionError,
)


class TestComfyUIPipe:
    
    def setup_method(self):
        """Set up test fixtures"""
        self.config = {
            "host": "127.0.0.1",
            "port": 8188,
            "workflow_file": "test_workflow.json",
            "field_mappings": [
                ["test_value", "3.inputs.seed", "int"],
                ["prompt_text", "6.inputs.text"],
            ],
            "timeout": 30,
            "secure": False,
        }
        
        self.pipe = ComfyUIPipe(self.config)
        
        # Create a sample workflow
        self.sample_workflow = {
            "3": {
                "inputs": {
                    "seed": 0,
                    "steps": 20
                },
                "class_type": "KSampler"
            },
            "6": {
                "inputs": {
                    "text": "",
                    "clip": ["4", 1]
                },
                "class_type": "CLIPTextEncode"
            }
        }
        
        # Create sample pipe input
        self.pipe_input = PipeInput(
            input={
                "generation": {
                    "prompts": {
                        "p_prompt": "a beautiful landscape",
                        "n_prompt": "ugly, blurry"
                    }
                },
                "preset": {
                    "seed": 12345,
                    "resolution": {
                        "width": 512,
                        "height": 512
                    }
                }
            }
        )

    def test_load_workflow_file(self):
        """Test loading workflow from file"""
        with patch('builtins.open', create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = json.dumps(self.sample_workflow)
            
            with patch('pathlib.Path.exists', return_value=True):
                workflow = self.pipe.load_workflow_file()
                
        assert workflow == self.sample_workflow

    def test_load_workflow_file_not_found(self):
        """Test error when workflow file doesn't exist"""
        with patch('pathlib.Path.exists', return_value=False):
            with pytest.raises(FileNotFoundError):
                self.pipe.load_workflow_file()

    def test_set_nested_value(self):
        """Test setting nested dictionary values"""
        obj = {"a": {"b": {"c": 0}}}
        self.pipe.set_nested_value(obj, "a.b.c", 42)
        assert obj["a"]["b"]["c"] == 42
        
        # Test creating missing keys
        obj = {}
        self.pipe.set_nested_value(obj, "x.y.z", "value")
        assert obj["x"]["y"]["z"] == "value"

    def test_cast_value(self):
        """Test value type casting"""
        # Test int casting
        assert self.pipe.cast_value("42", "int") == 42
        assert self.pipe.cast_value(42.5, "int") == 42
        
        # Test float casting
        assert self.pipe.cast_value("3.14", "float") == 3.14
        assert self.pipe.cast_value(3, "float") == 3.0
        
        # Test bool casting
        assert self.pipe.cast_value("true", "bool")
        assert self.pipe.cast_value("1", "bool")
        assert not (self.pipe.cast_value("false", "bool"))
        assert not (self.pipe.cast_value("0", "bool"))
        
        # Test str casting
        assert self.pipe.cast_value(42, "str") == "42"
        
        # Test None handling
        assert self.pipe.cast_value(None, "int") is None

    @pytest.mark.asyncio
    async def test_apply_field_mappings(self):
        """Test applying field mappings to workflow"""
        workflow = {
            "3": {"inputs": {"seed": 0}},
            "6": {"inputs": {"text": ""}}
        }

        # Field mappings with already-processed values
        self.pipe.config["field_mappings"] = [
            ["12345", "3.inputs.seed", "int"],
            ["test prompt", "6.inputs.text"]
        ]

        generation_outputs = Mock()
        result = await self.pipe.apply_field_mappings(workflow, self.pipe_input, generation_outputs)

        assert result["3"]["inputs"]["seed"] == 12345
        assert result["6"]["inputs"]["text"] == "test prompt"

    @pytest.mark.asyncio
    async def test_apply_field_mappings_invalid(self):
        """Test handling invalid field mappings"""
        workflow = {"3": {"inputs": {}}}
        generation_outputs = Mock()

        # Test empty mapping
        self.pipe.config["field_mappings"] = [[]]
        result = await self.pipe.apply_field_mappings(workflow, self.pipe_input, generation_outputs)
        assert result == workflow

        # Test mapping with only one element
        self.pipe.config["field_mappings"] = [["source"]]
        result = await self.pipe.apply_field_mappings(workflow, self.pipe_input, generation_outputs)
        assert result == workflow

    @pytest.mark.asyncio
    async def test_apply_field_mappings_image_required_raises_when_the_file_is_missing(self):
        """A "image_required" mapping (the importer's own primary source-image
        field - see backend/preset_import/emit.py's `_field_mapping_entry`)
        must never let the workflow's own baked-in placeholder filename reach
        ComfyUI unnoticed: with no real file at the mapped value, this must
        raise instead of silently leaving the node's original input alone."""
        workflow = {"78": {"inputs": {"image": "workflow_placeholder.png"}}}
        self.pipe.config["field_mappings"] = [
            ["not_a_real_uploaded_file.png", "78.inputs.image", "image_required"],
        ]
        generation_outputs = Mock()

        with pytest.raises(GenerationExecutionError):
            await self.pipe.apply_field_mappings(workflow, self.pipe_input, generation_outputs)

    @pytest.mark.asyncio
    async def test_apply_field_mappings_image_optional_missing_file_is_skipped_not_raised(self):
        """Confirms the raise above is specific to "image_required": an
        ordinary optional "image" mapping (a reference image the admin
        didn't provide) keeps its pre-existing behavior - the node is left
        as-is and the mapping is silently skipped, since `node_manipulations`
        removes that node from the submitted workflow anyway."""
        workflow = {"42": {"inputs": {"image": "workflow_placeholder.png"}}}
        self.pipe.config["field_mappings"] = [
            ["", "42.inputs.image", "image"],
        ]
        generation_outputs = Mock()

        result = await self.pipe.apply_field_mappings(workflow, self.pipe_input, generation_outputs)

        assert result["42"]["inputs"]["image"] == "workflow_placeholder.png"

    @pytest.mark.asyncio
    async def test_apply_field_mappings_video_required_raises_when_the_file_is_missing(self):
        """Same guarantee as `image_required`, for the importer's video
        upload fields (LoadVideo, ... - see `defaults._video_item`, which is
        always required)."""
        workflow = {"10": {"inputs": {"file": "workflow_placeholder.mp4"}}}
        self.pipe.config["field_mappings"] = [
            ["not_a_real_uploaded_file.mp4", "10.inputs.file", "video_required"],
        ]
        generation_outputs = Mock()

        with pytest.raises(GenerationExecutionError):
            await self.pipe.apply_field_mappings(workflow, self.pipe_input, generation_outputs)

    @pytest.mark.asyncio
    async def test_apply_field_mappings_audio_required_raises_when_the_file_is_missing(self):
        """Same guarantee, for the importer's audio upload fields (LoadAudio,
        ... - see `defaults._audio_item`, which is always required)."""
        workflow = {"10": {"inputs": {"audio": "workflow_placeholder.wav"}}}
        self.pipe.config["field_mappings"] = [
            ["not_a_real_uploaded_file.wav", "10.inputs.audio", "audio_required"],
        ]
        generation_outputs = Mock()

        with pytest.raises(GenerationExecutionError):
            await self.pipe.apply_field_mappings(workflow, self.pipe_input, generation_outputs)

    @pytest.mark.asyncio
    @patch('websockets.connect', new_callable=AsyncMock)
    async def test_connect_websocket(self, mock_connect):
        """Test WebSocket connection.

        `websockets.connect(...)` is wrapped in `asyncio.wait_for(...)`, so the
        patched replacement must itself be awaitable when called (unlike
        aiohttp's `session.post()`, which is sync and returns an async context
        manager) - `new_callable=AsyncMock` gives a call that returns a
        coroutine resolving to `mock_connect.return_value`.
        """
        mock_ws = AsyncMock()
        mock_connect.return_value = mock_ws

        generation_outputs = Mock()

        result = await self.pipe.connect_websocket(generation_outputs)

        assert result
        assert self.pipe.ws is not None
        assert self.pipe.client_id is not None

        # Check that progress was reported
        generation_outputs.assert_called()

    @pytest.mark.asyncio
    @patch('websockets.connect', new_callable=AsyncMock)
    async def test_connect_websocket_timeout(self, mock_connect):
        """A connection timeout is a real transport failure - it must raise
        GenerationExecutionError (so the generation is marked FAILED) instead
        of being swallowed into a falsy return the caller silently ignores."""
        mock_connect.side_effect = asyncio.TimeoutError()

        generation_outputs = Mock()

        with pytest.raises(GenerationExecutionError) as ctx:
            await self.pipe.connect_websocket(generation_outputs)

        assert "connection timed out" in str(ctx.value)
        assert self.pipe.ws is None

    @pytest.mark.asyncio
    @patch('aiohttp.ClientSession')
    async def test_submit_workflow(self, mock_session_class):
        """Test workflow submission"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={'prompt_id': 'test-prompt-id'})
        
        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value.__aenter__.return_value = mock_session
        
        self.pipe.client_id = "test-client"
        generation_outputs = Mock()
        
        prompt_id = await self.pipe.submit_workflow(self.sample_workflow, generation_outputs)
        
        assert prompt_id == 'test-prompt-id'
        generation_outputs.assert_called()

    @pytest.mark.asyncio
    @patch('aiohttp.ClientSession')
    async def test_submit_workflow_error(self, mock_session_class):
        """A non-200 submission response is a real transport failure - it must
        raise GenerationExecutionError carrying ComfyUI's own status and body,
        instead of being swallowed into a None the caller silently ignores."""
        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.text = AsyncMock(return_value="Bad request")

        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value.__aenter__.return_value = mock_session

        self.pipe.client_id = "test-client"
        generation_outputs = Mock()

        with pytest.raises(GenerationExecutionError) as ctx:
            await self.pipe.submit_workflow(self.sample_workflow, generation_outputs)

        assert "400" in str(ctx.value)
        assert "Bad request" in str(ctx.value)

    @pytest.mark.asyncio
    async def test_listen_for_updates_executing(self):
        """Test handling executing messages"""
        self.pipe.total_nodes = 2
        self.pipe.executed_nodes = 0
        
        # Mock WebSocket
        mock_ws = AsyncMock()
        messages = [
            json.dumps({
                "type": "executing",
                "data": {"node": "3"}
            }),
            json.dumps({
                "type": "executing",
                "data": {"node": None}  # Finished
            })
        ]
        mock_ws.recv = AsyncMock(side_effect=messages)
        self.pipe.ws = mock_ws
        
        generation_outputs = Mock()

        images, videos = await self.pipe.listen_for_updates("test-prompt", generation_outputs)

        assert self.pipe.executed_nodes == 1
        assert len(images) == 0

    @pytest.mark.asyncio
    @patch.object(ComfyUIPipe, 'load_image_from_comfy')
    async def test_listen_for_updates_with_images(self, mock_load_image):
        """Test handling executed messages with images"""
        self.pipe.total_nodes = 1
        
        # Create a test image
        test_image = Image.new('RGB', (100, 100))
        mock_load_image.return_value = test_image
        
        # Mock WebSocket
        mock_ws = AsyncMock()
        messages = [
            json.dumps({
                "type": "executed",
                "data": {
                    "output": {
                        "images": [
                            {"filename": "test.png", "type": "output"}
                        ]
                    }
                }
            }),
            json.dumps({
                "type": "executing",
                "data": {"node": None}  # Finished
            })
        ]
        mock_ws.recv = AsyncMock(side_effect=messages)
        self.pipe.ws = mock_ws
        
        generation_outputs = Mock()

        images, videos = await self.pipe.listen_for_updates("test-prompt", generation_outputs)

        assert len(images) == 1
        assert images[0] == test_image
        
        # Check that ImageGenerationOutput was called
        calls = generation_outputs.call_args_list
        image_output_called = any(
            isinstance(call[0][0], ImageGenerationOutput) 
            for call in calls
        )
        assert image_output_called

    @pytest.mark.asyncio
    async def test_listen_for_updates_error(self):
        """A ComfyUI execution_error raises GenerationExecutionError (a real
        failure) instead of being swallowed as a progress line, so the
        generation is marked FAILED and the user is notified."""
        # Mock WebSocket
        mock_ws = AsyncMock()
        messages = [
            json.dumps({
                "type": "execution_error",
                "data": {
                    "node_id": "12",
                    "node_type": "KSampler",
                    "exception_type": "RuntimeError",
                    "exception_message": "Test error",
                    "traceback": ["Traceback (most recent call last):", "  RuntimeError: Test error"],
                }
            })
        ]
        mock_ws.recv = AsyncMock(side_effect=messages)
        self.pipe.ws = mock_ws

        generation_outputs = Mock()

        with pytest.raises(GenerationExecutionError) as ctx:
            await self.pipe.listen_for_updates("test-prompt", generation_outputs)

        # The exception carries the rich detail body (node + traceback)
        assert str(ctx.value) == "Test error"
        assert "Node 12 (KSampler)" in ctx.value.detail
        assert "RuntimeError: Test error" in ctx.value.detail

    @pytest.mark.asyncio
    async def test_async_process_raises_on_empty_result(self):
        """A ComfyUI run that completes with no execution_error but produces
        no images or videos must still fail the generation - otherwise it is
        indistinguishable from a real (if boring) success."""
        with patch.object(self.pipe, 'load_workflow_file', return_value=self.sample_workflow), \
             patch.object(self.pipe, 'connect_websocket', new=AsyncMock(return_value=True)), \
             patch.object(self.pipe, 'submit_workflow', new=AsyncMock(return_value='prompt-1')), \
             patch.object(self.pipe, 'listen_for_updates', new=AsyncMock(return_value=([], []))), \
             patch.object(self.pipe, 'disconnect_websocket', new=AsyncMock()):

            generation_outputs = Mock()

            with pytest.raises(GenerationExecutionError) as ctx:
                await self.pipe._async_process(self.pipe_input, generation_outputs)

        assert "produced no images or videos" in str(ctx.value)

    def test_process_raises_when_comfyui_unreachable(self):
        """process() is the entry point GenerationEngine calls. A ComfyUI
        server that refuses the connection must fail the generation (raise),
        not return an empty PipeOutput that looks like a zero-image success."""
        with patch.object(self.pipe, 'load_workflow_file', return_value=self.sample_workflow), \
             patch('websockets.connect', new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = ConnectionRefusedError("refused")

            generation_outputs = Mock()

            with pytest.raises(GenerationExecutionError) as ctx:
                self.pipe.process(self.pipe_input, generation_outputs)

        assert "Could not connect to ComfyUI" in str(ctx.value)

    def test_format_execution_error(self):
        """_format_execution_error builds a node + traceback body from ComfyUI's payload."""
        detail = ComfyUIPipe._format_execution_error({
            "node_id": "7",
            "node_type": "VAEDecode",
            "exception_type": "ValueError",
            "exception_message": "bad tensor",
            "traceback": ["line 1", "line 2"],
        })
        assert "Node 7 (VAEDecode)" in detail
        assert "ValueError: bad tensor" in detail
        assert "line 1" in detail
        assert "line 2" in detail

    def test_format_execution_error_empty(self):
        """An empty payload yields None (no body)."""
        assert ComfyUIPipe._format_execution_error({}) is None

    @pytest.mark.asyncio
    @patch('aiohttp.ClientSession')
    async def test_load_image_from_comfy(self, mock_session_class):
        """Test loading image from ComfyUI"""
        # Create test image bytes
        img = Image.new('RGB', (100, 100), color='red')
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')
        img_bytes.seek(0)
        
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.read = AsyncMock(return_value=img_bytes.getvalue())
        
        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value.__aenter__.return_value = mock_session
        
        image_data = {
            "filename": "test.png",
            "type": "output",
            "subfolder": ""
        }
        
        result = await self.pipe.load_image_from_comfy(image_data)
        
        assert result is not None
        assert isinstance(result, Image.Image)

    @pytest.mark.asyncio
    async def test_disconnect_websocket(self):
        """Test WebSocket disconnection"""
        mock_ws = AsyncMock()
        self.pipe.ws = mock_ws
        
        await self.pipe.disconnect_websocket()
        
        mock_ws.close.assert_called_once()
        assert self.pipe.ws is None

    def test_configuration(self):
        """Test configuration specifications"""
        config_specs = ComfyUIPipe.configuration()

        # Check that all required configuration parameters are defined
        param_names = [spec.name for spec in config_specs]
        assert "host" in param_names
        assert "port" in param_names
        assert "workflow_file" in param_names
        assert "field_mappings" in param_names
        assert "node_manipulations" in param_names
        assert "timeout" in param_names
        assert "secure" in param_names

        # Check workflow_file is required
        workflow_spec = next(s for s in config_specs if s.name == "workflow_file")
        assert workflow_spec.required

        # Check field_mappings is required
        mappings_spec = next(s for s in config_specs if s.name == "field_mappings")
        assert mappings_spec.required

        # Check node_manipulations is optional
        manipulations_spec = next(s for s in config_specs if s.name == "node_manipulations")
        assert not manipulations_spec.required

    def test_inputs_outputs(self):
        """Test input and output specifications"""
        inputs = ComfyUIPipe.inputs()
        outputs = ComfyUIPipe.outputs()

        # Check inputs
        input_names = [i.name for i in inputs]
        assert "conditioning" in input_names
        assert "seed" in input_names
        assert "image" in input_names

        # Check outputs
        output_names = [o.name for o in outputs]
        assert "image" in output_names

        # Check that image output is array
        image_output = next(o for o in outputs if o.name == "image")
        assert image_output.is_array

    def test_remove_node(self):
        """Test removing a node from workflow"""
        workflow = {
            "1": {"inputs": {"text": "test"}},
            "2": {"inputs": {"value": ["1", 0]}},  # References node 1
            "3": {"inputs": {"image": ["2", 0]}}   # References node 2
        }

        # Remove node 2
        self.pipe.remove_node(workflow, "2")

        # Check node 2 is removed
        assert "2" not in workflow

        # Check node 3's reference to node 2 is cleared
        assert workflow["3"]["inputs"]["image"] is None

        # Node 1 should still exist
        assert "1" in workflow

    def test_remove_node_not_found(self):
        """Test removing a non-existent node"""
        workflow = {"1": {"inputs": {"text": "test"}}}

        # Should not raise, just log warning
        self.pipe.remove_node(workflow, "999")

        # Workflow should be unchanged
        assert len(workflow) == 1
        assert "1" in workflow

    def test_bypass_node(self):
        """Test bypassing a node"""
        workflow = {
            "1": {"inputs": {"text": "test"}},
            "2": {"inputs": {"frames": ["1", 0]}},  # Connects from node 1
            "3": {"inputs": {"image": ["2", 0]}}     # Connects to node 2
        }

        # Bypass node 2
        self.pipe.bypass_node(workflow, "2")

        # Node 2 should be removed
        assert "2" not in workflow

        # Node 3 should now connect directly to node 1
        assert workflow["3"]["inputs"]["image"] == ["1", 0]

    def test_bypass_node_no_input(self):
        """Test bypassing a node with no input connections"""
        workflow = {
            "1": {"inputs": {"text": "test"}},
            "2": {"inputs": {"value": 5}},       # No connection, just a value
            "3": {"inputs": {"image": ["2", 0]}} # Connects to node 2
        }

        # Bypass node 2 (should clear connections and remove node)
        self.pipe.bypass_node(workflow, "2")

        # Node 2 should be removed since it had outgoing connections
        assert "2" not in workflow

        # Node 3's connection should be cleared
        assert workflow["3"]["inputs"]["image"] is None

    def test_reroute_connection(self):
        """Test rerouting connections between nodes"""
        workflow = {
            "1": {"inputs": {"text": "test"}},
            "2": {"inputs": {"value": ["1", 0]}},  # From node 1
            "3": {"inputs": {}},                   # Empty, will be new target
            "4": {"inputs": {"data": ["1", 0]}}    # Also from node 1
        }

        # Reroute connections from node 1 to node 2, redirect to node 3
        self.pipe.reroute_connection(workflow, "1", "2", "3")

        # Node 3 should now have the connection
        assert workflow["3"]["inputs"]["value"] == ["1", 0]

        # Node 4 should be unchanged (different target)
        assert workflow["4"]["inputs"]["data"] == ["1", 0]

    def test_evaluate_condition(self):
        """Test condition evaluation"""
        pipe_input = PipeInput(input={})

        # Test various boolean strings
        assert self.pipe.evaluate_condition("true", pipe_input)
        assert self.pipe.evaluate_condition("True", pipe_input)
        assert self.pipe.evaluate_condition("1", pipe_input)
        assert self.pipe.evaluate_condition("yes", pipe_input)

        assert not (self.pipe.evaluate_condition("false", pipe_input))
        assert not (self.pipe.evaluate_condition("False", pipe_input))
        assert not (self.pipe.evaluate_condition("0", pipe_input))
        assert not (self.pipe.evaluate_condition("no", pipe_input))

        # Test empty (no condition = always true)
        assert self.pipe.evaluate_condition("", pipe_input)
        # Test None (template processing failure = default to False for safety)
        assert not (self.pipe.evaluate_condition(None, pipe_input))

        # Test boolean values
        assert self.pipe.evaluate_condition(True, pipe_input)
        assert not (self.pipe.evaluate_condition(False, pipe_input))

    @pytest.mark.asyncio
    async def test_apply_node_manipulations(self):
        """Test applying multiple node manipulations"""
        workflow = {
            "1": {"inputs": {"text": "test"}},
            # bypass_node only follows connections through its recognized input
            # keys (images/samples/latent/frames/image - see
            # ComfyUIPipe.bypass_node) - "image" here, not an arbitrary key,
            # is what makes node 2 bypassable below.
            "2": {"inputs": {"image": ["1", 0]}},
            "3": {"inputs": {"image": ["2", 0]}},
            "4": {"inputs": {"data": "static"}}
        }

        # Configure manipulations
        self.pipe.config["node_manipulations"] = [
            {
                "type": "remove_node",
                "node_id": "4",
                "condition": "true"
            },
            {
                "type": "bypass_node",
                "node_id": "2",
                "condition": "true"
            },
            {
                "type": "remove_node",
                "node_id": "999",  # Non-existent, should not fail
                "condition": "true"
            },
            {
                "type": "remove_node",
                "node_id": "1",
                "condition": "false"  # Should skip due to condition
            }
        ]

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        result = await self.pipe.apply_node_manipulations(workflow, pipe_input, generation_outputs)

        # Node 4 should be removed
        assert "4" not in result

        # Node 2 should be bypassed (removed)
        assert "2" not in result

        # Node 3 should connect to node 1 (bypass of node 2)
        assert result["3"]["inputs"]["image"] == ["1", 0]

        # Node 1 should still exist (condition was false)
        assert "1" in result

        # Original workflow should be unchanged (deep copy)
        assert "2" in workflow
        assert "4" in workflow

    @pytest.mark.asyncio
    async def test_apply_node_manipulations_invalid_type(self):
        """Test handling of invalid manipulation type"""
        workflow = {"1": {"inputs": {"text": "test"}}}

        self.pipe.config["node_manipulations"] = [
            {
                "type": "invalid_type",
                "node_id": "1",
                "condition": "true"
            }
        ]

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        # Should not crash, just log warning
        result = await self.pipe.apply_node_manipulations(workflow, pipe_input, generation_outputs)

        # Workflow should be unchanged
        assert len(result) == 1
        assert "1" in result

    @pytest.mark.asyncio
    async def test_apply_node_manipulations_empty(self):
        """Test with no manipulations configured"""
        workflow = {"1": {"inputs": {"text": "test"}}}

        # No manipulations configured
        self.pipe.config["node_manipulations"] = []

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        result = await self.pipe.apply_node_manipulations(workflow, pipe_input, generation_outputs)

        # Should return unchanged workflow
        assert result == workflow

    def test_add_node(self):
        """Test adding a node to workflow"""
        workflow = {
            "1": {"class_type": "ExistingNode", "inputs": {}}
        }

        node_config = {
            "class_type": "NewNode",
            "inputs": {"param": "value"},
            "_meta": {"title": "Test Node"}
        }

        self.pipe.add_node(workflow, "2", node_config)

        # Node should be added
        assert "2" in workflow
        assert workflow["2"] == node_config

    def test_add_node_overwrite(self):
        """Test adding a node that already exists"""
        workflow = {
            "1": {"class_type": "ExistingNode", "inputs": {"old": "data"}}
        }

        new_config = {
            "class_type": "NewNode",
            "inputs": {"new": "data"}
        }

        self.pipe.add_node(workflow, "1", new_config)

        # Node should be overwritten
        assert workflow["1"] == new_config

    @pytest.mark.asyncio
    async def test_apply_node_manipulations_add_node(self):
        """Test apply_node_manipulations with add_node operation"""
        workflow = {
            "1": {"class_type": "ExistingNode", "inputs": {}}
        }

        manipulations = [
            {
                "type": "add_node",
                "node_id": "2",
                "condition": "true",
                "node_config": {
                    "class_type": "TorchCompileModelWanVideo",
                    "inputs": {
                        "backend": "inductor",
                        "mode": "max-autotune",
                        "model": ["1", 0]
                    },
                    "_meta": {"title": "Compile Node"}
                }
            }
        ]

        self.pipe.config["node_manipulations"] = manipulations

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        result = await self.pipe.apply_node_manipulations(workflow, pipe_input, generation_outputs)

        # Original node should still exist
        assert "1" in result
        # New node should be added
        assert "2" in result
        assert result["2"]["class_type"] == "TorchCompileModelWanVideo"
        assert result["2"]["inputs"]["backend"] == "inductor"

    @pytest.mark.asyncio
    async def test_apply_node_manipulations_add_node_missing_params(self):
        """Test add_node with missing parameters"""
        workflow = {"1": {"inputs": {"text": "test"}}}

        manipulations = [
            {
                "type": "add_node",
                "node_id": "2"
                # Missing node_config
            }
        ]

        self.pipe.config["node_manipulations"] = manipulations

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        result = await self.pipe.apply_node_manipulations(workflow, pipe_input, generation_outputs)

        # Should not add the node due to missing config
        assert "2" not in result
        assert len(result) == 1

    def test_update_node_input(self):
        """Test updating a node input"""
        workflow = {
            "1": {
                "class_type": "TestNode",
                "inputs": {"old_param": "old_value"}
            }
        }

        self.pipe.update_node_input(workflow, "1", "model", ["2", 0])

        # Input should be updated
        assert workflow["1"]["inputs"]["model"] == ["2", 0]
        # Old input should still exist
        assert workflow["1"]["inputs"]["old_param"] == "old_value"

    def test_update_node_input_missing_node(self):
        """Test updating input for non-existent node"""
        workflow = {"1": {"inputs": {}}}

        self.pipe.update_node_input(workflow, "999", "model", ["2", 0])

        # Should not crash, and original workflow unchanged
        assert len(workflow) == 1
        assert "999" not in workflow

    def test_update_node_input_no_inputs(self):
        """Test updating input for node without inputs section"""
        workflow = {
            "1": {"class_type": "TestNode"}
        }

        self.pipe.update_node_input(workflow, "1", "model", ["2", 0])

        # Should create inputs section and add the input
        assert "inputs" in workflow["1"]
        assert workflow["1"]["inputs"]["model"] == ["2", 0]

    @pytest.mark.asyncio
    async def test_apply_node_manipulations_update_node_input(self):
        """Test apply_node_manipulations with update_node_input operation"""
        workflow = {
            "1": {
                "class_type": "Sampler",
                "inputs": {"model": ["old_model", 0]}
            }
        }

        manipulations = [
            {
                "type": "update_node_input",
                "condition": "true",
                "node_id": "1",
                "input_key": "model",
                "input_value": ["new_model", 0]
            }
        ]

        self.pipe.config["node_manipulations"] = manipulations

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        result = await self.pipe.apply_node_manipulations(workflow, pipe_input, generation_outputs)

        # Node should have updated input
        assert result["1"]["inputs"]["model"] == ["new_model", 0]

    @pytest.mark.asyncio
    async def test_apply_node_manipulations_update_node_input_missing_params(self):
        """Test update_node_input with missing parameters"""
        workflow = {"1": {"inputs": {"model": ["old", 0]}}}

        manipulations = [
            {
                "type": "update_node_input",
                "node_id": "1",
                "input_key": "model"
                # Missing input_value
            }
        ]

        self.pipe.config["node_manipulations"] = manipulations

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        result = await self.pipe.apply_node_manipulations(workflow, pipe_input, generation_outputs)

        # Should not update due to missing parameters
        assert result["1"]["inputs"]["model"] == ["old", 0]

    # ==================== Node Name Resolution Tests ====================

    def test_build_node_name_mapping(self):
        """Test building name-to-ID mapping from workflow"""
        workflow = {
            "1": {
                "inputs": {},
                "class_type": "KSampler",
                "_meta": {"title": "My Sampler"}
            },
            "2": {
                "inputs": {},
                "class_type": "CLIPLoader",
                "_meta": {"title": "Load CLIP"}
            },
            "3": {
                "inputs": {},
                "class_type": "VAELoader"
                # No _meta - should be skipped
            }
        }

        name_to_id, duplicates = self.pipe.build_node_name_mapping(workflow)

        assert name_to_id["My Sampler"] == "1"
        assert name_to_id["Load CLIP"] == "2"
        assert "VAELoader" not in name_to_id  # No title, not in mapping
        assert len(duplicates) == 0  # No duplicates

    def test_build_node_name_mapping_with_duplicates(self):
        """Test that duplicate node names are tracked"""
        workflow = {
            "1": {
                "inputs": {},
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "CLIP Text Encode"}
            },
            "2": {
                "inputs": {},
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "CLIP Text Encode"}  # Same name
            },
            "3": {
                "inputs": {},
                "class_type": "KSampler",
                "_meta": {"title": "Sampler"}
            }
        }

        name_to_id, duplicates = self.pipe.build_node_name_mapping(workflow)

        # "CLIP Text Encode" should be in duplicates, not in name_to_id
        assert "CLIP Text Encode" in duplicates
        assert "1" in duplicates["CLIP Text Encode"]
        assert "2" in duplicates["CLIP Text Encode"]
        # "Sampler" should be in name_to_id (unique)
        assert name_to_id["Sampler"] == "3"

    def test_resolve_node_reference_by_name(self):
        """Test resolving a node reference by name"""
        name_to_id = {"My Sampler": "1", "Load CLIP": "2"}
        duplicates = {}

        resolved = self.pipe.resolve_node_reference("My Sampler", name_to_id, duplicates)
        assert resolved == "1"

        resolved = self.pipe.resolve_node_reference("Load CLIP", name_to_id, duplicates)
        assert resolved == "2"

    def test_resolve_node_reference_by_id(self):
        """Test that node IDs are passed through unchanged"""
        name_to_id = {"My Sampler": "1"}
        duplicates = {}

        # ID "3" is not in name_to_id, should be returned as-is
        resolved = self.pipe.resolve_node_reference("3", name_to_id, duplicates)
        assert resolved == "3"

    def test_resolve_node_reference_duplicate_error(self):
        """Test that using a duplicate name raises an error"""
        name_to_id = {"Unique Name": "3"}
        duplicates = {"CLIP Text Encode": ["1", "2"]}

        with pytest.raises(ValueError) as context:
            self.pipe.resolve_node_reference("CLIP Text Encode", name_to_id, duplicates)

        assert "CLIP Text Encode" in str(context.value)
        assert "1" in str(context.value)
        assert "2" in str(context.value)

    def test_resolve_connection_references(self):
        """Test resolving node names in connection arrays"""
        name_to_id = {"Load CLIP": "2", "VAE Loader": "3"}
        duplicates = {}

        node_config = {
            "inputs": {
                "clip": ["Load CLIP", 0],
                "vae": ["VAE Loader", 1],
                "steps": 20  # Non-connection input
            },
            "class_type": "KSampler"
        }

        resolved = self.pipe.resolve_connection_references(node_config, name_to_id, duplicates)

        assert resolved["inputs"]["clip"] == ["2", 0]
        assert resolved["inputs"]["vae"] == ["3", 1]
        assert resolved["inputs"]["steps"] == 20  # Unchanged

    def test_resolve_connection_references_with_ids(self):
        """Test that existing node IDs in connections are preserved"""
        name_to_id = {}
        duplicates = {}

        node_config = {
            "inputs": {
                "model": ["5", 0],  # Already an ID
                "latent": ["10", 1]
            },
            "class_type": "KSampler"
        }

        resolved = self.pipe.resolve_connection_references(node_config, name_to_id, duplicates)

        # IDs should be unchanged
        assert resolved["inputs"]["model"] == ["5", 0]
        assert resolved["inputs"]["latent"] == ["10", 1]

    def test_set_nested_value_with_name_resolution(self):
        """Test set_nested_value with node name resolution"""
        workflow = {
            "1": {
                "inputs": {"seed": 0},
                "class_type": "KSampler",
                "_meta": {"title": "My Sampler"}
            }
        }
        name_to_id = {"My Sampler": "1"}
        duplicates = {}

        self.pipe.set_nested_value(workflow, "My Sampler.inputs.seed", 42, name_to_id, duplicates)

        assert workflow["1"]["inputs"]["seed"] == 42

    def test_set_nested_value_without_name_resolution(self):
        """Test set_nested_value still works without name mapping (backward compatibility)"""
        workflow = {
            "1": {"inputs": {"seed": 0}}
        }

        # Call without name_to_id parameter
        self.pipe.set_nested_value(workflow, "1.inputs.seed", 42)

        assert workflow["1"]["inputs"]["seed"] == 42

    @pytest.mark.asyncio
    async def test_apply_field_mappings_with_node_names(self):
        """Test apply_field_mappings with node names instead of IDs"""
        workflow = {
            "1": {
                "inputs": {"seed": 0},
                "class_type": "KSampler",
                "_meta": {"title": "My Sampler"}
            },
            "2": {
                "inputs": {"text": ""},
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "Positive Prompt"}
            }
        }

        self.pipe.config["field_mappings"] = [
            ["12345", "My Sampler.inputs.seed", "int"],
            ["hello world", "Positive Prompt.inputs.text", "str"]
        ]

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        result = await self.pipe.apply_field_mappings(workflow, pipe_input, generation_outputs)

        assert result["1"]["inputs"]["seed"] == 12345
        assert result["2"]["inputs"]["text"] == "hello world"

    @pytest.mark.asyncio
    async def test_apply_field_mappings_mixed_names_and_ids(self):
        """Test apply_field_mappings with both node names and IDs"""
        workflow = {
            "1": {
                "inputs": {"seed": 0},
                "class_type": "KSampler",
                "_meta": {"title": "My Sampler"}
            },
            "2": {
                "inputs": {"text": ""},
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "Prompt"}
            }
        }

        self.pipe.config["field_mappings"] = [
            ["12345", "My Sampler.inputs.seed", "int"],  # Using name
            ["hello", "2.inputs.text", "str"]  # Using ID
        ]

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        result = await self.pipe.apply_field_mappings(workflow, pipe_input, generation_outputs)

        assert result["1"]["inputs"]["seed"] == 12345
        assert result["2"]["inputs"]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_apply_field_mappings_duplicate_name_error(self):
        """A field mapping targeting an ambiguous (duplicate) node name is
        skipped, not raised: apply_field_mappings wraps each mapping in its
        own try/except (so one bad mapping can't abort the rest of the
        workflow) - the ambiguous nodes are left at their default values.
        The ValueError itself is real and still verified directly against
        resolve_node_reference by test_resolve_node_reference_duplicate_error.
        """
        workflow = {
            "1": {
                "inputs": {"text": ""},
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "CLIP Encode"}
            },
            "2": {
                "inputs": {"text": ""},
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "CLIP Encode"}  # Duplicate name
            }
        }

        self.pipe.config["field_mappings"] = [
            ["test", "CLIP Encode.inputs.text", "str"]  # Using duplicate name
        ]

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        result = await self.pipe.apply_field_mappings(workflow, pipe_input, generation_outputs)

        assert result["1"]["inputs"]["text"] == ""
        assert result["2"]["inputs"]["text"] == ""

    @pytest.mark.asyncio
    async def test_apply_node_manipulations_remove_by_name(self):
        """Test remove_node manipulation using node name"""
        workflow = {
            "1": {
                "inputs": {},
                "class_type": "KSampler",
                "_meta": {"title": "My Sampler"}
            },
            "2": {
                "inputs": {"model": ["1", 0]},
                "class_type": "VAEDecode"
            }
        }

        self.pipe.config["node_manipulations"] = [
            {
                "type": "remove_node",
                "node_id": "My Sampler"  # Using name instead of "1"
            }
        ]

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        result = await self.pipe.apply_node_manipulations(workflow, pipe_input, generation_outputs)

        assert "1" not in result
        assert "2" in result

    @pytest.mark.asyncio
    async def test_apply_node_manipulations_add_node_with_name_connections(self):
        """Test add_node with node names in connection arrays"""
        workflow = {
            "1": {
                "inputs": {},
                "class_type": "UNETLoader",
                "_meta": {"title": "Load Model"}
            }
        }

        self.pipe.config["node_manipulations"] = [
            {
                "type": "add_node",
                "node_id": "200",
                "node_config": {
                    "inputs": {
                        "model": ["Load Model", 0],  # Name reference
                        "strength": 1.0
                    },
                    "class_type": "LoraLoader",
                    "_meta": {"title": "My LoRA"}
                }
            }
        ]

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        result = await self.pipe.apply_node_manipulations(workflow, pipe_input, generation_outputs)

        assert "200" in result
        # Connection should be resolved to ID "1"
        assert result["200"]["inputs"]["model"] == ["1", 0]

    @pytest.mark.asyncio
    async def test_apply_node_manipulations_update_with_name_connection(self):
        """Test update_node_input with node name in connection value"""
        workflow = {
            "1": {
                "inputs": {},
                "class_type": "UNETLoader",
                "_meta": {"title": "Model Loader"}
            },
            "2": {
                "inputs": {"model": ["old", 0]},
                "class_type": "KSampler",
                "_meta": {"title": "Sampler"}
            }
        }

        self.pipe.config["node_manipulations"] = [
            {
                "type": "update_node_input",
                "node_id": "Sampler",  # Using name
                "input_key": "model",
                "input_value": ["Model Loader", 0]  # Using name in connection
            }
        ]

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        result = await self.pipe.apply_node_manipulations(workflow, pipe_input, generation_outputs)

        # Both node_id and connection should be resolved
        assert result["2"]["inputs"]["model"] == ["1", 0]

    @pytest.mark.asyncio
    async def test_apply_node_manipulations_bypass_by_name(self):
        """Test bypass_node manipulation using node name"""
        workflow = {
            "1": {
                "inputs": {},
                "class_type": "UNETLoader",
                "_meta": {"title": "Model"}
            },
            "2": {
                "inputs": {"images": ["1", 0]},
                "class_type": "Upscaler",
                "_meta": {"title": "My Upscaler"}
            },
            "3": {
                "inputs": {"images": ["2", 0]},
                "class_type": "SaveImage"
            }
        }

        self.pipe.config["node_manipulations"] = [
            {
                "type": "bypass_node",
                "node_id": "My Upscaler"  # Using name
            }
        ]

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        result = await self.pipe.apply_node_manipulations(workflow, pipe_input, generation_outputs)

        # Node 2 should be removed
        assert "2" not in result
        # Node 3 should now connect directly to node 1
        assert result["3"]["inputs"]["images"] == ["1", 0]


    @pytest.mark.asyncio
    async def test_listen_for_updates_binary_preview(self):
        """Test handling binary preview images from ComfyUI WebSocket"""
        self.pipe.total_nodes = 2
        self.pipe.executed_nodes = 0

        # Create a test preview image (JPEG format, as ComfyUI typically uses)
        test_image = Image.new('RGB', (100, 100), color='blue')
        img_bytes = io.BytesIO()
        test_image.save(img_bytes, format='JPEG')
        img_bytes.seek(0)
        image_data = img_bytes.getvalue()

        # Create binary preview message with ComfyUI format:
        # - First 4 bytes: message type as big-endian uint32 (1 = preview image)
        # - Next 4 bytes: format type as big-endian uint32 (1 = JPEG)
        # - Rest: image data
        msg_type = (1).to_bytes(4, byteorder='big')  # 00 00 00 01
        format_type = (1).to_bytes(4, byteorder='big')  # 00 00 00 01 (JPEG)
        binary_message = msg_type + format_type + image_data

        # Mock WebSocket
        mock_ws = AsyncMock()
        messages = [
            binary_message,  # Binary preview image
            json.dumps({
                "type": "executing",
                "data": {"node": None}  # Finished
            })
        ]
        mock_ws.recv = AsyncMock(side_effect=messages)
        self.pipe.ws = mock_ws

        generation_outputs = Mock()

        images, videos = await self.pipe.listen_for_updates("test-prompt", generation_outputs)

        # Check that ImageGenerationOutput was called with temporary=True for the preview
        calls = generation_outputs.call_args_list
        preview_output_called = any(
            isinstance(call[0][0], ImageGenerationOutput) and
            call[0][0].temporary == True
            for call in calls
        )
        assert preview_output_called, "Binary preview should generate ImageGenerationOutput with temporary=True"

    @pytest.mark.asyncio
    async def test_listen_for_updates_invalid_binary(self):
        """Test that invalid binary data is handled gracefully"""
        self.pipe.total_nodes = 1

        # Create invalid binary message (message type 5 which is not recognized)
        msg_type = (5).to_bytes(4, byteorder='big')  # 00 00 00 05 - unknown type
        format_type = (1).to_bytes(4, byteorder='big')
        invalid_binary = msg_type + format_type + b"not an image"

        # Mock WebSocket
        mock_ws = AsyncMock()
        messages = [
            invalid_binary,  # Invalid binary message (unknown type, will try to decode as text)
            json.dumps({
                "type": "executing",
                "data": {"node": None}  # Finished
            })
        ]
        mock_ws.recv = AsyncMock(side_effect=messages)
        self.pipe.ws = mock_ws

        generation_outputs = Mock()

        # Should not raise an exception
        images, videos = await self.pipe.listen_for_updates("test-prompt", generation_outputs)

        # Should complete without error
        assert len(images) == 0
        assert len(videos) == 0


class TestComfyUIPipeSamplingProgress:
    """Test ComfyUI sampling step progress emission"""

    def setup_method(self):
        self.config = {
            "host": "127.0.0.1",
            "port": 8188,
            "workflow_file": "test_workflow.json",
            "field_mappings": [],
            "timeout": 30,
            "secure": False,
        }
        self.pipe = ComfyUIPipe(self.config)

    @pytest.mark.asyncio
    async def test_progress_message_emits_step_progress_with_known_node(self):
        """Test that progress messages emit ProgressGenerationOutput with node info"""
        self.pipe.total_nodes = 2
        self.pipe.executed_nodes = 0
        self.pipe.current_node = "3"
        self.pipe.workflow_nodes = {
            "3": {
                'id': '3',
                'class_type': 'KSampler',
                'title': 'KSampler',
                'inputs': {'steps': 20}
            }
        }

        mock_ws = AsyncMock()
        messages = [
            json.dumps({
                "type": "progress",
                "data": {"value": 5, "max": 20}
            }),
            json.dumps({
                "type": "executing",
                "data": {"node": None}  # Finished
            })
        ]
        mock_ws.recv = AsyncMock(side_effect=messages)
        self.pipe.ws = mock_ws

        generation_outputs = Mock()

        await self.pipe.listen_for_updates("test-prompt", generation_outputs)

        # Find the ProgressGenerationOutput calls with step info
        step_progress_calls = [
            call[0][0] for call in generation_outputs.call_args_list
            if isinstance(call[0][0], ProgressGenerationOutput)
            and "Step 5/20" in call[0][0].state
        ]
        assert len(step_progress_calls) == 1
        step_output = step_progress_calls[0]

        # Should contain PIPE template with node title and icon
        assert "<<PIPE:KSampler:bolt>>" in step_output.state
        assert "Step 5/20" in step_output.state
        assert "<<PROGRESS:25%:chart>>" in step_output.state
        # Check progress values
        assert step_output.progress.current == 25
        assert step_output.progress.max == 100

    @pytest.mark.asyncio
    async def test_progress_message_emits_step_progress_with_unknown_node(self):
        """Test that progress messages use fallback text when node is unknown"""
        self.pipe.total_nodes = 2
        self.pipe.executed_nodes = 0
        self.pipe.current_node = "999"  # Not in workflow_nodes
        self.pipe.workflow_nodes = {}

        mock_ws = AsyncMock()
        messages = [
            json.dumps({
                "type": "progress",
                "data": {"value": 10, "max": 30}
            }),
            json.dumps({
                "type": "executing",
                "data": {"node": None}
            })
        ]
        mock_ws.recv = AsyncMock(side_effect=messages)
        self.pipe.ws = mock_ws

        generation_outputs = Mock()

        await self.pipe.listen_for_updates("test-prompt", generation_outputs)

        step_progress_calls = [
            call[0][0] for call in generation_outputs.call_args_list
            if isinstance(call[0][0], ProgressGenerationOutput)
            and "Sampling step" in call[0][0].state
        ]
        assert len(step_progress_calls) == 1
        step_output = step_progress_calls[0]

        assert "Sampling step 10/30" in step_output.state
        assert "<<PROGRESS:33%:chart>>" in step_output.state
        # Should NOT contain PIPE template
        assert "<<PIPE:" not in step_output.state

    @pytest.mark.asyncio
    async def test_progress_message_fallback_when_no_current_node(self):
        """Test fallback when current_node is None"""
        self.pipe.total_nodes = 1
        self.pipe.executed_nodes = 0
        self.pipe.current_node = None
        self.pipe.workflow_nodes = {}

        mock_ws = AsyncMock()
        messages = [
            json.dumps({
                "type": "progress",
                "data": {"value": 1, "max": 10}
            }),
            json.dumps({
                "type": "executing",
                "data": {"node": None}
            })
        ]
        mock_ws.recv = AsyncMock(side_effect=messages)
        self.pipe.ws = mock_ws

        generation_outputs = Mock()

        await self.pipe.listen_for_updates("test-prompt", generation_outputs)

        step_progress_calls = [
            call[0][0] for call in generation_outputs.call_args_list
            if isinstance(call[0][0], ProgressGenerationOutput)
            and "Sampling step" in call[0][0].state
        ]
        assert len(step_progress_calls) == 1
        assert "Sampling step 1/10" in step_progress_calls[0].state

    @pytest.mark.asyncio
    async def test_executing_sets_current_node(self):
        """Test that executing messages set self.current_node"""
        self.pipe.total_nodes = 3
        self.pipe.executed_nodes = 0
        self.pipe.workflow_nodes = {
            "3": {
                'id': '3',
                'class_type': 'KSampler',
                'title': 'KSampler',
                'inputs': {}
            }
        }

        mock_ws = AsyncMock()
        messages = [
            json.dumps({
                "type": "executing",
                "data": {"node": "3"}
            }),
            json.dumps({
                "type": "progress",
                "data": {"value": 5, "max": 20}
            }),
            json.dumps({
                "type": "executing",
                "data": {"node": None}
            })
        ]
        mock_ws.recv = AsyncMock(side_effect=messages)
        self.pipe.ws = mock_ws

        generation_outputs = Mock()

        await self.pipe.listen_for_updates("test-prompt", generation_outputs)

        # current_node should have been set to "3" by the executing message
        # and the progress message should use that node info
        step_progress_calls = [
            call[0][0] for call in generation_outputs.call_args_list
            if isinstance(call[0][0], ProgressGenerationOutput)
            and "Step 5/20" in call[0][0].state
        ]
        assert len(step_progress_calls) == 1
        assert "<<PIPE:KSampler:bolt>>" in step_progress_calls[0].state

    @pytest.mark.asyncio
    async def test_progress_with_zero_max_steps(self):
        """Test progress handling when max_steps is 0"""
        self.pipe.total_nodes = 1
        self.pipe.executed_nodes = 0
        self.pipe.current_node = None
        self.pipe.workflow_nodes = {}

        mock_ws = AsyncMock()
        messages = [
            json.dumps({
                "type": "progress",
                "data": {"value": 0, "max": 0}
            }),
            json.dumps({
                "type": "executing",
                "data": {"node": None}
            })
        ]
        mock_ws.recv = AsyncMock(side_effect=messages)
        self.pipe.ws = mock_ws

        generation_outputs = Mock()

        await self.pipe.listen_for_updates("test-prompt", generation_outputs)

        step_progress_calls = [
            call[0][0] for call in generation_outputs.call_args_list
            if isinstance(call[0][0], ProgressGenerationOutput)
            and "Sampling step" in call[0][0].state
        ]
        assert len(step_progress_calls) == 1
        assert "<<PROGRESS:0%:chart>>" in step_progress_calls[0].state


class TestComfyUIPipeVideoUpload:
    """Test ComfyUIPipe video upload and video type field mapping"""

    def setup_method(self):
        self.config = {
            "host": "127.0.0.1",
            "port": 8188,
            "workflow_file": "test_workflow.json",
            "field_mappings": [],
            "timeout": 30,
            "secure": False,
        }
        self.pipe = ComfyUIPipe(self.config)

    @pytest.mark.asyncio
    @patch('aiohttp.ClientSession')
    async def test_upload_video_to_comfyui_success(self, mock_session_class):
        """Test successful video upload to ComfyUI"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={'name': 'uploaded_video.mp4'})

        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value.__aenter__.return_value = mock_session

        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f:
            f.write(b'\x00\x00\x00\x1cftypisom')  # Minimal MP4 header bytes
            video_path = Path(f.name)

        try:
            generation_outputs = Mock()
            result = await self.pipe.upload_video_to_comfyui(video_path, generation_outputs)

            assert result == 'uploaded_video.mp4'

            # Verify the upload was called with correct URL
            mock_session.post.assert_called_once()
            call_args = mock_session.post.call_args
            assert '/upload/image' in call_args[0][0]
        finally:
            video_path.unlink()

    @pytest.mark.asyncio
    @patch('aiohttp.ClientSession')
    async def test_upload_video_to_comfyui_failure(self, mock_session_class):
        """Test video upload failure"""
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal server error")

        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value.__aenter__.return_value = mock_session

        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f:
            f.write(b'\x00\x00\x00\x1cftypisom')
            video_path = Path(f.name)

        try:
            generation_outputs = Mock()
            result = await self.pipe.upload_video_to_comfyui(video_path, generation_outputs)

            assert result is None
            # Should report error via generation_outputs
            generation_outputs.assert_called()
        finally:
            video_path.unlink()

    @pytest.mark.asyncio
    @patch('aiohttp.ClientSession')
    async def test_upload_video_content_type_detection(self, mock_session_class):
        """Test that correct content type is detected from file extension"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={'name': 'test.webm'})

        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__.return_value = mock_response
        mock_session_class.return_value.__aenter__.return_value = mock_session

        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as f:
            f.write(b'\x1a\x45\xdf\xa3')  # WebM magic bytes
            video_path = Path(f.name)

        try:
            generation_outputs = Mock()
            await self.pipe.upload_video_to_comfyui(video_path, generation_outputs)

            # Verify upload was called
            mock_session.post.assert_called_once()
        finally:
            video_path.unlink()

    @pytest.mark.asyncio
    async def test_field_mapping_video_type_file_not_found(self):
        """Test video type field mapping when file doesn't exist"""
        workflow = {
            "21": {"inputs": {"file": "default.mp4"}, "class_type": "LoadVideo"}
        }

        self.pipe.config["field_mappings"] = [
            ["/nonexistent/video.mp4", "21.inputs.file", "video"]
        ]

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        result = await self.pipe.apply_field_mappings(workflow, pipe_input, generation_outputs)

        # Should keep default since upload failed
        assert result["21"]["inputs"]["file"] == "default.mp4"

    @pytest.mark.asyncio
    @patch.object(ComfyUIPipe, 'upload_video_to_comfyui')
    async def test_field_mapping_video_type_success(self, mock_upload):
        """Test video type field mapping with successful upload"""
        mock_upload.return_value = "uploaded_video.mp4"

        workflow = {
            "21": {"inputs": {"file": "default.mp4"}, "class_type": "LoadVideo"}
        }

        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f:
            f.write(b'\x00\x00\x00\x1cftypisom')
            video_path = f.name

        try:
            self.pipe.config["field_mappings"] = [
                [video_path, "21.inputs.file", "video"]
            ]

            pipe_input = PipeInput(input={})
            generation_outputs = Mock()

            result = await self.pipe.apply_field_mappings(workflow, pipe_input, generation_outputs)

            # Should be replaced with uploaded filename
            assert result["21"]["inputs"]["file"] == "uploaded_video.mp4"
            mock_upload.assert_called_once()
        finally:
            Path(video_path).unlink()

    @pytest.mark.asyncio
    async def test_field_mapping_video_type_invalid_value(self):
        """Test video type field mapping with non-string value"""
        workflow = {
            "21": {"inputs": {"file": "default.mp4"}, "class_type": "LoadVideo"}
        }

        self.pipe.config["field_mappings"] = [
            [12345, "21.inputs.file", "video"]  # Non-string value
        ]

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        result = await self.pipe.apply_field_mappings(workflow, pipe_input, generation_outputs)

        # Should keep default since value was not a string
        assert result["21"]["inputs"]["file"] == "default.mp4"


class TestComfyUIPipeBackendConfig(unittest.TestCase):
    """Test ComfyUIPipe backend config injection"""

    def test_backend_config_from_self_config(self):
        """Test that ComfyUIPipe reads backend_config from self.config"""
        # Config with backend_config injected (as ComfyUIBackend would do)
        config = {
            "host": "127.0.0.1",  # Default preset value
            "port": 8188,         # Default preset value
            "workflow_file": "test_workflow.json",
            "field_mappings": [],
            "timeout": 30,
            "secure": False,
            "backend_config": {    # Injected by ComfyUIBackend
                "host": "192.168.1.100",
                "port": 9999,
                "secure": True,
                "client_id": "backend-client-id",
                "api_key": "secret-key",
                "timeout": 600
            }
        }

        pipe = ComfyUIPipe(config)

        # Verify backend_config is accessible from self.config
        backend_config = pipe.config.get('backend_config', {})
        self.assertEqual(backend_config.get('host'), "192.168.1.100")
        self.assertEqual(backend_config.get('port'), 9999)
        self.assertEqual(backend_config.get('secure'), True)
        self.assertEqual(backend_config.get('client_id'), "backend-client-id")

    def test_backend_config_overrides_defaults(self):
        """Test that backend_config properly overrides defaults when preset doesn't specify"""
        # Config where preset uses default values (should be overridden by backend_config)
        config = {
            "host": "127.0.0.1",  # Default value - should be overridden
            "port": 8188,         # Default value - should be overridden
            "workflow_file": "test.json",
            "field_mappings": [],
            "timeout": 30,
            "secure": None,       # Not specified - should use backend_config
            "client_id": None,    # Not specified - should use backend_config
            "backend_config": {
                "host": "comfyui.example.com",
                "port": 443,
                "secure": True,
                "client_id": "test-client"
            }
        }

        pipe = ComfyUIPipe(config)

        # Call process to trigger config merging
        # We need to mock the workflow loading and async execution
        with patch.object(pipe, 'load_workflow_file', return_value={"1": {"inputs": {}}}):
            with patch('asyncio.new_event_loop') as mock_loop, patch('asyncio.set_event_loop'):
                mock_loop.return_value.run_until_complete = MagicMock(return_value=([], []))
                mock_loop.return_value.close = MagicMock()

                pipe_input = PipeInput(input={})
                generation_outputs = Mock()

                # This will trigger the config merging in process()
                pipe.process(pipe_input, generation_outputs)

                # After process(), self.config should have merged values
                # Backend values should override defaults
                self.assertEqual(pipe.config.get('host'), "comfyui.example.com")
                self.assertEqual(pipe.config.get('port'), 443)
                self.assertEqual(pipe.config.get('secure'), True)
                self.assertEqual(pipe.config.get('client_id'), "test-client")

    def test_preset_config_takes_precedence(self):
        """Test that preset-specified config overrides backend_config"""
        # Config where preset explicitly specifies different values
        config = {
            "host": "preset-host.example.com",  # Preset specified - should NOT be overridden
            "port": 7777,                        # Preset specified - should NOT be overridden
            "workflow_file": "test.json",
            "field_mappings": [],
            "timeout": 60,
            "secure": False,
            "client_id": "preset-client",
            "backend_config": {
                "host": "backend-host.example.com",
                "port": 443,
                "secure": True,
                "client_id": "backend-client"
            }
        }

        pipe = ComfyUIPipe(config)

        with patch.object(pipe, 'load_workflow_file', return_value={"1": {"inputs": {}}}):
            with patch('asyncio.new_event_loop') as mock_loop, patch('asyncio.set_event_loop'):
                mock_loop.return_value.run_until_complete = MagicMock(return_value=([], []))
                mock_loop.return_value.close = MagicMock()

                pipe_input = PipeInput(input={})
                generation_outputs = Mock()
                pipe.process(pipe_input, generation_outputs)

                # Preset values should take precedence (they're non-default)
                # But the current logic only overrides default values
                # So preset-host should be kept if != 127.0.0.1
                self.assertEqual(pipe.config.get('host'), "preset-host.example.com")
                self.assertEqual(pipe.config.get('port'), 7777)


if __name__ == '__main__':
    unittest.main()

class TestMediaPathResolution:
    """A form media value reaches the pipe in one of two relative conventions
    (CWD-relative with the storage prefix, or storage-root-relative as the
    history picker stores it); the second must resolve through the SETTINGS
    service exactly like core's media_loader, never leave the workflow's own
    placeholder filename in the node."""

    def _pipe(self):
        return ComfyUIPipe({"host": "127.0.0.1", "port": 8188, "workflow_file": "w.json", "field_mappings": []})

    @staticmethod
    def _settings(storage_root):
        settings = Mock()
        settings.get_file_storage_directory.return_value = str(storage_root)
        return settings

    @pytest.mark.asyncio
    async def test_storage_root_relative_image_is_resolved_and_uploaded(self, tmp_path):
        storage = tmp_path / "storage"
        image_dir = storage / "generations" / "2026-09-05" / "gen"
        image_dir.mkdir(parents=True)
        Image.new("RGB", (4, 4)).save(image_dir / "0.png")

        pipe = self._pipe()
        pipe.config["field_mappings"] = [["generations/2026-09-05/gen/0.png", "78.inputs.image", "image"]]
        pipe.upload_image_to_comfyui = AsyncMock(return_value="uploaded.png")
        workflow = {"78": {"inputs": {"image": "workflow_placeholder.png"}}}

        result = await pipe.apply_field_mappings(workflow, PipeInput(input={"SETTINGS": self._settings(storage)}), Mock())

        assert result["78"]["inputs"]["image"] == "uploaded.png"
        pipe.upload_image_to_comfyui.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_storage_root_relative_video_is_resolved_and_uploaded(self, tmp_path):
        storage = tmp_path / "storage"
        (storage / "uploads").mkdir(parents=True)
        (storage / "uploads" / "clip.mp4").write_bytes(b"x")

        pipe = self._pipe()
        pipe.config["field_mappings"] = [["uploads/clip.mp4", "10.inputs.file", "video_required"]]
        pipe.upload_video_to_comfyui = AsyncMock(return_value="clip.mp4")
        workflow = {"10": {"inputs": {"file": "workflow_placeholder.mp4"}}}

        result = await pipe.apply_field_mappings(workflow, PipeInput(input={"SETTINGS": self._settings(storage)}), Mock())

        assert result["10"]["inputs"]["file"] == "clip.mp4"
        assert pipe.upload_video_to_comfyui.await_args.args[0] == storage / "uploads" / "clip.mp4"

    @pytest.mark.asyncio
    async def test_required_image_that_resolves_nowhere_still_raises(self, tmp_path):
        pipe = self._pipe()
        pipe.config["field_mappings"] = [["generations/missing/0.png", "78.inputs.image", "image_required"]]
        workflow = {"78": {"inputs": {"image": "workflow_placeholder.png"}}}

        with pytest.raises(GenerationExecutionError):
            await pipe.apply_field_mappings(workflow, PipeInput(input={"SETTINGS": self._settings(tmp_path)}), Mock())

    @pytest.mark.asyncio
    async def test_without_the_settings_service_a_cwd_relative_path_still_works(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "storage" / "uploads").mkdir(parents=True)
        Image.new("RGB", (4, 4)).save(tmp_path / "storage" / "uploads" / "a.png")

        pipe = self._pipe()
        pipe.config["field_mappings"] = [["storage/uploads/a.png", "78.inputs.image", "image"]]
        pipe.upload_image_to_comfyui = AsyncMock(return_value="a.png")
        workflow = {"78": {"inputs": {"image": "workflow_placeholder.png"}}}

        result = await pipe.apply_field_mappings(workflow, PipeInput(input={}), Mock())

        assert result["78"]["inputs"]["image"] == "a.png"
