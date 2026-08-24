"""Tests for build_graph() - the pure pipes -> graph projection."""

import pytest
from unittest.mock import Mock

from src.pipelines.graph import (
    build_graph,
    PipelineGraph,
    PipeNode,
    PipeConnection,
)


class TestBuildGraph:
    """Tests for build_graph()."""

    @pytest.fixture
    def mock_pipe_catalog(self):
        """Mock PipeCatalog."""
        return Mock()

    def test_build_graph_success(self, mock_pipe_catalog):
        """Test successful graph building from processed pipes."""
        pipes = [
            {
                "name": "downloader",
                "enabled": True,
                "config": {"models": []},
                "input": [],
            },
            {
                "name": "generator",
                "enabled": True,
                "config": {"steps": 20},
                "input": [
                    {"name": "model", "provider": "downloader", "output_var": "model", "enabled": True}
                ],
            },
        ]

        mock_pipe_class = Mock()
        mock_pipe_class.inputs.return_value = []
        mock_pipe_class.outputs.return_value = []
        mock_pipe_class.description = "Test pipe"
        mock_pipe_catalog.get_pipe.return_value = mock_pipe_class

        result = build_graph(pipes, mock_pipe_catalog, preset_id="test-preset", mode="txt2img")

        assert isinstance(result, PipelineGraph)
        assert result.preset_id == "test-preset"
        assert result.mode == "txt2img"
        assert len(result.nodes) == 2
        assert len(result.connections) == 1

        assert result.connections[0].source_node == "downloader"
        assert result.connections[0].target_node == "generator"

    def test_build_graph_disabled_pipes(self, mock_pipe_catalog):
        """Test that disabled pipes don't get pipe_id."""
        pipes = [
            {"name": "enabled_pipe", "enabled": True, "config": {}, "input": []},
            {"name": "disabled_pipe", "enabled": False, "config": {}, "input": []},
            {"name": "another_enabled", "enabled": True, "config": {}, "input": []},
        ]

        mock_pipe_class = Mock()
        mock_pipe_class.inputs.return_value = []
        mock_pipe_class.outputs.return_value = []
        mock_pipe_class.description = "Test"
        mock_pipe_catalog.get_pipe.return_value = mock_pipe_class

        result = build_graph(pipes, mock_pipe_catalog, preset_id="test-preset", mode="txt2img")

        assert result.nodes[0].pipe_id == 0  # enabled
        assert result.nodes[1].pipe_id is None  # disabled
        assert result.nodes[2].pipe_id == 1  # enabled

    def test_build_graph_missing_pipe_class(self, mock_pipe_catalog):
        """Test graph building when pipe class is not found."""
        pipes = [
            {"name": "unknown_pipe", "enabled": True, "config": {"test": "config"}, "input": []},
        ]

        mock_pipe_catalog.get_pipe.return_value = None

        result = build_graph(pipes, mock_pipe_catalog, preset_id="test-preset", mode="txt2img")

        assert len(result.nodes) == 1
        assert result.nodes[0].status == "not_found"
        assert result.nodes[0].name == "unknown_pipe"

    def test_build_graph_empty_pipes(self, mock_pipe_catalog):
        """Test graph building with an empty pipe list."""
        result = build_graph([], mock_pipe_catalog, preset_id="test-preset", mode="txt2img")

        assert result.nodes == []
        assert result.connections == []


class TestPipelineGraph:
    """Tests for PipelineGraph dataclass."""

    def test_debug_info(self):
        """Test debug_info property."""
        result = PipelineGraph(
            preset_id="test",
            mode="txt2img",
            nodes=[
                PipeNode(
                    id="1", name="pipe1", description="", enabled=True,
                    position={"x": 0, "y": 0}, inputs=[], outputs=[],
                    configuration={}, status="available", pipe_id=0, template_index=0
                ),
                PipeNode(
                    id="2", name="pipe2", description="", enabled=False,
                    position={"x": 0, "y": 0}, inputs=[], outputs=[],
                    configuration={}, status="available", pipe_id=None, template_index=1
                ),
                PipeNode(
                    id="3", name="pipe3", description="", enabled=True,
                    position={"x": 0, "y": 0}, inputs=[], outputs=[],
                    configuration={}, status="not_found", pipe_id=1, template_index=2
                ),
            ],
            connections=[],
        )

        debug_info = result.debug_info

        assert debug_info["total_pipes"] == 3
        assert debug_info["available_pipes"] == 2
        assert debug_info["missing_pipes"] == 1
        assert debug_info["enabled_pipes"] == 2
        assert debug_info["disabled_pipes"] == 1

    def test_to_dict(self):
        """Test to_dict method."""
        result = PipelineGraph(
            preset_id="test",
            mode="txt2img",
            nodes=[],
            connections=[],
        )

        data = result.to_dict()

        assert data["preset_id"] == "test"
        assert data["mode"] == "txt2img"
        assert data["nodes"] == []
        assert data["connections"] == []
        assert "debug_info" in data


class TestPipeNode:
    """Tests for PipeNode dataclass."""

    def test_to_dict(self):
        """Test to_dict method."""
        node = PipeNode(
            id="test-id",
            name="test-name",
            description="Test description",
            enabled=True,
            position={"x": 100, "y": 50},
            inputs=[{"name": "input1", "type": "image"}],
            outputs=[{"name": "output1", "type": "image"}],
            configuration={"steps": 20},
            status="available",
            pipe_id=0,
            template_index=1,
        )

        data = node.to_dict()

        assert data["id"] == "test-id"
        assert data["name"] == "test-name"
        assert data["description"] == "Test description"
        assert data["enabled"] is True
        assert data["position"] == {"x": 100, "y": 50}
        assert data["inputs"] == [{"name": "input1", "type": "image"}]
        assert data["outputs"] == [{"name": "output1", "type": "image"}]
        assert data["configuration"] == {"steps": 20}
        assert data["status"] == "available"
        assert data["pipe_id"] == 0
        assert data["template_index"] == 1


class TestPipeConnection:
    """Tests for PipeConnection dataclass."""

    def test_to_dict(self):
        """Test to_dict method."""
        connection = PipeConnection(
            id="connection-1",
            source_node="downloader",
            source_output="model",
            target_node="generator",
            target_input="input_model",
        )

        data = connection.to_dict()

        assert data["id"] == "connection-1"
        assert data["source_node"] == "downloader"
        assert data["source_output"] == "model"
        assert data["target_node"] == "generator"
        assert data["target_input"] == "input_model"
