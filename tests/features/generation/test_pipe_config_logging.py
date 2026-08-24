"""A pipe's configuration is written to the debug log verbatim.

Preset YAML supplies that configuration, and a preset targeting a remote
backend carries an `api_key` in it - so the debug log was printing a live
credential in full, into whatever collects the server's stdout. These tests
drive the real pipe loop and assert the credential never reaches a log record.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import pytest

from src.features.generation.generation import GenerationManager
from src.platform.plugins.registry import PluginRegistry
from src.pipelines.contracts import IOType, PipeInput, PipeOutput, PipeOutputSpec
from unittest.mock import Mock

API_KEY = "sk-live-51-do-not-log-this"


class _RemotePipe:
    """A pipe whose preset-supplied config carries a backend credential."""

    name = "remote_pipe"

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @staticmethod
    def get_default_config():
        return {"api_key": None, "url": "https://comfy.example.test", "steps": 20}

    @staticmethod
    def inputs():
        return []

    @staticmethod
    def outputs():
        return [PipeOutputSpec(name="output", io_type=IOType.TEXT, is_array=False)]

    @staticmethod
    def configuration():
        return []

    def process(self, pipe_input: PipeInput, generation_outputs: callable, is_cancelled=None):
        return PipeOutput(output={"output": "ok"})


def _mock_deps():
    return {
        "gpu": Mock(),
        "model_manager": Mock(),
        "pipe_catalog": Mock(),
        "settings_manager": Mock(),
        "system_monitor": Mock(),
        "memory_manager": Mock(),
        "llm_service": Mock(),
    }


def _run(caplog, config: Dict[str, Any]):
    deps = _mock_deps()
    registry = PluginRegistry(
        marketplace_dir="test_plugins_mp", local_dir="test_plugins_local"
    )
    manager = GenerationManager(**deps, plugin_registry=registry)
    deps["pipe_catalog"].get_pipe.return_value = _RemotePipe

    # The generation logger is "is"; the config line is DEBUG.
    with caplog.at_level(logging.DEBUG, logger="is"):
        manager.generate(
            [{
                "name": "remote_pipe", "enabled": True,
                "input": [], "cache": [], "config": config,
            }],
            lambda _o: None,
            "gen-logging",
        )
    return caplog.text


def test_a_preset_supplied_api_key_never_reaches_the_log(caplog):
    text = _run(caplog, {"api_key": API_KEY})

    assert API_KEY not in text
    assert "***" in text


def test_a_nested_credential_never_reaches_the_log(caplog):
    """A backend block is a dict inside the config, so the walk has to recurse."""
    nested = "sk-nested-do-not-log-this"
    text = _run(caplog, {"backend_config": {"api_key": nested, "url": "https://x.test"}})

    assert nested not in text


def test_the_rest_of_the_configuration_is_still_logged(caplog):
    """Redaction that ate the whole line would take the debugging value with it."""
    text = _run(caplog, {"api_key": API_KEY, "steps": 33})

    assert "remote_pipe" in text
    assert "'steps': 33" in text
    assert "https://comfy.example.test" in text


def test_the_pipe_still_receives_the_real_credential(caplog):
    """Redaction is for the log line only - the pipe needs the real key to work."""
    deps = _mock_deps()
    registry = PluginRegistry(
        marketplace_dir="test_plugins_mp", local_dir="test_plugins_local"
    )
    manager = GenerationManager(**deps, plugin_registry=registry)

    seen = {}

    class _CapturingPipe(_RemotePipe):
        def process(self, pipe_input, generation_outputs, is_cancelled=None):
            seen.update(self.config)
            return PipeOutput(output={"output": "ok"})

    deps["pipe_catalog"].get_pipe.return_value = _CapturingPipe

    manager.generate(
        [{
            "name": "remote_pipe", "enabled": True,
            "input": [], "cache": [], "config": {"api_key": API_KEY},
        }],
        lambda _o: None,
        "gen-passthrough",
    )

    assert seen["api_key"] == API_KEY
