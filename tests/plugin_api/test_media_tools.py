from src.platform.media_tools import types
from src.plugin_api import media_tools


def test_a_plugin_tool_ends_its_run_with_the_host_s_own_cancel_and_failure_errors():
    assert media_tools.ToolCancelled is types.ToolCancelled
    assert media_tools.ToolFailed is types.ToolFailed
    assert media_tools.ToolFailed("No model file").message == "No model file"
    assert media_tools.ToolCancelled().code == "cancelled"
    assert {"ToolCancelled", "ToolFailed"} <= set(media_tools.__all__)
