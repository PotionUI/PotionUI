"""Tests for SDXLModel hook registry system."""

import pytest
from unittest.mock import MagicMock, patch
from src.pipelines.pipes.generator.sdxl.denoising_hook import DenoisingHook


class MockHook(DenoisingHook):
    """A concrete hook for testing."""
    def __init__(self, name: str = "test_hook", priority: int = 0):
        self.name = name
        self.priority = priority


def _make_model():
    """Create an SDXLModel with mocked dependencies."""
    with patch("src.pipelines.pipes.checkpoint_loader.sdxl.sdxl_model.Model.__init__"):
        from src.pipelines.pipes.checkpoint_loader.sdxl.sdxl_model import SDXLModel
        template = MagicMock()
        config = {}
        model = SDXLModel.__new__(SDXLModel)
        model.template = template
        model.scheduler = None
        model.controlnets = None
        model.using_controlnet = False
        model._inference_device = None
        model.model_type_info = None
        model.denoising_hooks = {}
        model.pipe = None
        model.config = config
        return model


class TestHookRegistration:
    """Tests for register_hook and get_ordered_hooks."""

    def test_register_hook_adds_to_dict(self):
        model = _make_model()
        hook = MockHook(name="adm", priority=10)
        model.register_hook("adm", hook)
        assert "adm" in model.denoising_hooks
        assert model.denoising_hooks["adm"] is hook

    def test_register_hook_overwrites_existing(self):
        model = _make_model()
        hook1 = MockHook(name="adm", priority=10)
        hook2 = MockHook(name="adm_v2", priority=20)
        model.register_hook("adm", hook1)
        model.register_hook("adm", hook2)
        assert model.denoising_hooks["adm"] is hook2

    def test_register_multiple_hooks(self):
        model = _make_model()
        hook_adm = MockHook(name="adm", priority=10)
        hook_sag = MockHook(name="sag", priority=50)
        hook_sharp = MockHook(name="sharpness", priority=90)
        model.register_hook("adm", hook_adm)
        model.register_hook("sag", hook_sag)
        model.register_hook("sharpness", hook_sharp)
        assert len(model.denoising_hooks) == 3

    def test_get_ordered_hooks_empty(self):
        model = _make_model()
        result = model.get_ordered_hooks()
        assert result == []

    def test_get_ordered_hooks_single(self):
        model = _make_model()
        hook = MockHook(name="adm", priority=10)
        model.register_hook("adm", hook)
        result = model.get_ordered_hooks()
        assert len(result) == 1
        assert result[0] is hook

    def test_get_ordered_hooks_sorted_by_priority(self):
        model = _make_model()
        hook_high = MockHook(name="sharpness", priority=90)
        hook_low = MockHook(name="adm", priority=10)
        hook_mid = MockHook(name="sag", priority=50)
        model.register_hook("sharpness", hook_high)
        model.register_hook("adm", hook_low)
        model.register_hook("sag", hook_mid)
        result = model.get_ordered_hooks()
        assert len(result) == 3
        assert result[0] is hook_low
        assert result[1] is hook_mid
        assert result[2] is hook_high

    def test_get_ordered_hooks_same_priority(self):
        """Hooks with the same priority should all be returned (order among them is stable but unspecified)."""
        model = _make_model()
        hook_a = MockHook(name="a", priority=10)
        hook_b = MockHook(name="b", priority=10)
        model.register_hook("a", hook_a)
        model.register_hook("b", hook_b)
        result = model.get_ordered_hooks()
        assert len(result) == 2
        # Both hooks present
        assert set(h.name for h in result) == {"a", "b"}

    def test_denoising_hooks_initialized_in_init(self):
        """Verify __init__ creates empty denoising_hooks dict."""
        model = _make_model()
        assert hasattr(model, "denoising_hooks")
        assert isinstance(model.denoising_hooks, dict)
        assert len(model.denoising_hooks) == 0
