import pytest
from unittest.mock import Mock
from PIL import Image
from pathlib import Path

from src.pipelines.pipes.output_skipper.main import OutputSkipperPipe
from src.pipelines.contracts import PipeInput, IOType


class TestOutputSkipperPipe:

    def create_test_data(self):
        """Create test data for various output types"""
        return {
            "video": [f"/path/to/video{i}.mp4" for i in range(5)],
            "image": [Image.new('RGB', (100, 100), (255, 0, 0)) for _ in range(4)],
            "seed": [12345, 67890, 11111, 22222, 33333],
            "text": ["result1", "result2", "result3"],
            "single_value": "single_item"
        }

    def test_pipe_initialization(self):
        """Test pipe initialization with default config"""
        pipe = OutputSkipperPipe({})

        assert pipe.name == "output_skipper"
        assert "Filter and skip specific outputs" in pipe.description

        # Check default config
        config = pipe.get_default_config()
        assert config["rules"] == []

    def test_configuration_spec(self):
        """Test configuration specification"""
        specs = OutputSkipperPipe.configuration()

        assert len(specs) == 1
        rules_spec = specs[0]
        assert rules_spec.name == "rules"
        assert rules_spec.param_type == list
        assert rules_spec.default == []

    def test_input_output_specs(self):
        """Test input and output specifications"""
        inputs = OutputSkipperPipe.inputs()
        outputs = OutputSkipperPipe.outputs()

        # Should have common IOTypes
        input_names = [inp.name for inp in inputs]
        output_names = [out.name for out in outputs]

        common_types = ["image", "video", "mask", "latent", "seed", "conditioning"]
        for type_name in common_types:
            assert type_name in input_names
            assert type_name in output_names

        # All should be arrays and optional inputs
        for inp in inputs:
            assert inp.is_array == True
            assert inp.required == False

        # All outputs should be arrays
        for out in outputs:
            assert out.is_array == True

    def test_parse_rule_valid(self):
        """Test parsing valid rules"""
        pipe = OutputSkipperPipe({})

        # Test with indices
        rule1 = {
            "output_type": "video",
            "action": "skip",
            "indices": [0, 2]
        }
        parsed1 = pipe.parse_rule(rule1)
        assert parsed1 is not None
        assert parsed1["output_type"] == "video"
        assert parsed1["action"] == "skip"
        assert parsed1["indices"] == [0, 2]
        assert parsed1["count"] is None

        # Test with count
        rule2 = {
            "output_type": "image",
            "action": "keep",
            "count": 3
        }
        parsed2 = pipe.parse_rule(rule2)
        assert parsed2 is not None
        assert parsed2["output_type"] == "image"
        assert parsed2["action"] == "keep"
        assert parsed2["count"] == 3
        assert parsed2["indices"] is None

    def test_parse_rule_invalid(self):
        """Test parsing invalid rules"""
        pipe = OutputSkipperPipe({})

        # Missing output_type
        rule1 = {"action": "skip", "indices": [0]}
        assert pipe.parse_rule(rule1) is None

        # Invalid action
        rule2 = {"output_type": "video", "action": "delete", "indices": [0]}
        assert pipe.parse_rule(rule2) is None

        # Missing indices and count
        rule3 = {"output_type": "video", "action": "skip"}
        assert pipe.parse_rule(rule3) is None

        # Invalid indices type
        rule4 = {"output_type": "video", "action": "skip", "indices": "not_a_list"}
        assert pipe.parse_rule(rule4) is None

        # Invalid count
        rule5 = {"output_type": "video", "action": "skip", "count": -1}
        assert pipe.parse_rule(rule5) is None

        # Not a dict
        assert pipe.parse_rule("invalid") is None

    def test_apply_rule_skip_by_indices(self):
        """Test applying skip rule with indices"""
        pipe = OutputSkipperPipe({})

        data = ["item0", "item1", "item2", "item3", "item4"]
        rule = {
            "output_type": "test",
            "action": "skip",
            "indices": [0, 2, 4],
            "count": None
        }

        result = pipe.apply_rule(data, rule)
        assert result == ["item1", "item3"]

    def test_apply_rule_keep_by_indices(self):
        """Test applying keep rule with indices"""
        pipe = OutputSkipperPipe({})

        data = ["item0", "item1", "item2", "item3", "item4"]
        rule = {
            "output_type": "test",
            "action": "keep",
            "indices": [1, 3],
            "count": None
        }

        result = pipe.apply_rule(data, rule)
        assert result == ["item1", "item3"]

    def test_apply_rule_skip_by_count(self):
        """Test applying skip rule with count"""
        pipe = OutputSkipperPipe({})

        data = ["item0", "item1", "item2", "item3", "item4"]
        rule = {
            "output_type": "test",
            "action": "skip",
            "indices": None,
            "count": 2
        }

        result = pipe.apply_rule(data, rule)
        assert result == ["item2", "item3", "item4"]

    def test_apply_rule_keep_by_count(self):
        """Test applying keep rule with count"""
        pipe = OutputSkipperPipe({})

        data = ["item0", "item1", "item2", "item3", "item4"]
        rule = {
            "output_type": "test",
            "action": "keep",
            "indices": None,
            "count": 3
        }

        result = pipe.apply_rule(data, rule)
        assert result == ["item0", "item1", "item2"]

    def test_apply_rule_empty_data(self):
        """Test applying rule to empty data"""
        pipe = OutputSkipperPipe({})

        rule = {
            "output_type": "test",
            "action": "skip",
            "indices": [0],
            "count": None
        }

        # Empty list
        assert pipe.apply_rule([], rule) == []

        # None data
        assert pipe.apply_rule(None, rule) is None

        # Non-list data
        assert pipe.apply_rule("single", rule) == "single"

    def test_apply_rule_out_of_bounds_indices(self):
        """Test applying rule with out of bounds indices"""
        pipe = OutputSkipperPipe({})

        data = ["item0", "item1"]
        rule = {
            "output_type": "test",
            "action": "skip",
            "indices": [0, 5, 10],  # 5 and 10 are out of bounds
            "count": None
        }

        result = pipe.apply_rule(data, rule)
        assert result == ["item1"]  # Only item0 (index 0) should be skipped

    def test_find_rule_for_output_type(self):
        """Test finding rules for specific output types"""
        pipe = OutputSkipperPipe({})

        rules = [
            {"output_type": "video", "action": "skip", "indices": [0]},
            {"output_type": "image", "action": "keep", "count": 2},
            {"output_type": "video", "action": "keep", "indices": [1]},  # Second video rule
        ]

        # Should find first matching rule
        video_rule = pipe.find_rule_for_output_type("video", rules)
        assert video_rule["action"] == "skip"
        assert video_rule["indices"] == [0]

        # Should find image rule
        image_rule = pipe.find_rule_for_output_type("image", rules)
        assert image_rule["action"] == "keep"
        assert image_rule["count"] == 2

        # Should return None for non-existent type
        text_rule = pipe.find_rule_for_output_type("text", rules)
        assert text_rule is None

    def test_process_no_rules(self):
        """Test processing with no rules (pass-through)"""
        pipe = OutputSkipperPipe({"rules": []})
        test_data = self.create_test_data()
        pipe_input = PipeInput(input=test_data)

        mock_outputs = Mock()
        result = pipe.process(pipe_input, mock_outputs)

        # Should pass through all data unchanged
        assert result.output == test_data

        # Should call generation_outputs
        assert mock_outputs.call_count > 0

    def test_process_invalid_rules(self):
        """Test processing with invalid rules (should pass-through)"""
        config = {
            "rules": [
                {"invalid": "rule"},
                {"output_type": "video", "action": "invalid_action", "indices": [0]}
            ]
        }
        pipe = OutputSkipperPipe(config)
        test_data = self.create_test_data()
        pipe_input = PipeInput(input=test_data)

        mock_outputs = Mock()
        result = pipe.process(pipe_input, mock_outputs)

        # Should pass through all data unchanged due to invalid rules
        assert result.output == test_data

    def test_process_skip_videos_by_indices(self):
        """Test processing with skip rule for videos by indices"""
        config = {
            "rules": [
                {
                    "output_type": "video",
                    "action": "skip",
                    "indices": [0, 2]  # Skip first and third video
                }
            ]
        }
        pipe = OutputSkipperPipe(config)
        test_data = self.create_test_data()
        pipe_input = PipeInput(input=test_data)

        mock_outputs = Mock()
        result = pipe.process(pipe_input, mock_outputs)

        # Videos should be filtered
        original_videos = test_data["video"]
        filtered_videos = result.output["video"]
        assert len(filtered_videos) == 3
        assert filtered_videos == [original_videos[1], original_videos[3], original_videos[4]]

        # Other data should pass through unchanged
        assert result.output["image"] == test_data["image"]
        assert result.output["seed"] == test_data["seed"]

    def test_process_keep_images_by_count(self):
        """Test processing with keep rule for images by count"""
        config = {
            "rules": [
                {
                    "output_type": "image",
                    "action": "keep",
                    "count": 2  # Keep first 2 images
                }
            ]
        }
        pipe = OutputSkipperPipe(config)
        test_data = self.create_test_data()
        pipe_input = PipeInput(input=test_data)

        mock_outputs = Mock()
        result = pipe.process(pipe_input, mock_outputs)

        # Images should be filtered to first 2
        filtered_images = result.output["image"]
        assert len(filtered_images) == 2
        assert filtered_images == test_data["image"][:2]

        # Other data should pass through unchanged
        assert result.output["video"] == test_data["video"]
        assert result.output["seed"] == test_data["seed"]

    def test_process_multiple_rules(self):
        """Test processing with multiple rules for different types"""
        config = {
            "rules": [
                {
                    "output_type": "video",
                    "action": "skip",
                    "indices": [0]  # Skip first video
                },
                {
                    "output_type": "seed",
                    "action": "keep",
                    "count": 3  # Keep first 3 seeds
                }
            ]
        }
        pipe = OutputSkipperPipe(config)
        test_data = self.create_test_data()
        pipe_input = PipeInput(input=test_data)

        mock_outputs = Mock()
        result = pipe.process(pipe_input, mock_outputs)

        # Videos should have first one skipped
        filtered_videos = result.output["video"]
        assert len(filtered_videos) == 4
        assert filtered_videos == test_data["video"][1:]

        # Seeds should be limited to first 3
        filtered_seeds = result.output["seed"]
        assert len(filtered_seeds) == 3
        assert filtered_seeds == test_data["seed"][:3]

        # Other data should pass through unchanged
        assert result.output["image"] == test_data["image"]
        assert result.output["text"] == test_data["text"]

    def test_process_single_item_data(self):
        """Test processing single items (non-list data)"""
        config = {
            "rules": [
                {
                    "output_type": "single_value",
                    "action": "skip",
                    "indices": [0]  # Skip the single item
                }
            ]
        }
        pipe = OutputSkipperPipe(config)
        test_data = self.create_test_data()
        pipe_input = PipeInput(input=test_data)

        mock_outputs = Mock()
        result = pipe.process(pipe_input, mock_outputs)

        # Single value should be skipped (becomes None)
        assert result.output["single_value"] is None

        # Other data should pass through unchanged
        assert result.output["video"] == test_data["video"]

    def test_process_edge_case_empty_list(self):
        """Test processing with empty lists"""
        config = {
            "rules": [
                {
                    "output_type": "empty_list",
                    "action": "keep",
                    "count": 2
                }
            ]
        }
        pipe = OutputSkipperPipe(config)
        test_data = {"empty_list": [], "video": ["/path/video1.mp4"]}
        pipe_input = PipeInput(input=test_data)

        mock_outputs = Mock()
        result = pipe.process(pipe_input, mock_outputs)

        # Empty list should remain empty
        assert result.output["empty_list"] == []
        assert result.output["video"] == test_data["video"]

    def test_realistic_comfyui_scenario(self):
        """Test realistic ComfyUI scenario: 2 videos, want only the second"""
        config = {
            "rules": [
                {
                    "output_type": "video",
                    "action": "skip",
                    "indices": [0]  # Skip first video, keep second
                }
            ]
        }
        pipe = OutputSkipperPipe(config)

        # Simulate ComfyUI returning 2 videos
        comfyui_output = {
            "video": ["/tmp/comfy_video_1.mp4", "/tmp/comfy_video_2.mp4"],
            "image": []  # No images in this scenario
        }
        pipe_input = PipeInput(input=comfyui_output)

        mock_outputs = Mock()
        result = pipe.process(pipe_input, mock_outputs)

        # Should only have the second video
        assert len(result.output["video"]) == 1
        assert result.output["video"][0] == "/tmp/comfy_video_2.mp4"
        assert result.output["image"] == []