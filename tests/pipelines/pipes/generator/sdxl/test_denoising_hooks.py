"""
Tests for the DenoisingHook base class and DenoisingContext.

Tests cover:
- DenoisingHook default methods return context unchanged
- Hook priority sorting
- DenoisingContext creation with all fields
- Hook lifecycle (pre_unet, post_unet, post_cfg)
- Custom hook that modifies context
"""
import unittest
from unittest.mock import Mock
import torch

from src.pipelines.pipes.generator.sdxl.denoising_hook import DenoisingHook, DenoisingContext


class TestDenoisingContext(unittest.TestCase):
    """Test DenoisingContext dataclass creation and field access."""

    def test_context_creation_with_all_fields(self):
        """Test that DenoisingContext can be created with all required fields."""
        unet = Mock()
        latent = torch.randn(2, 4, 64, 64)
        timestep = torch.tensor([500])
        prompt_embeds = torch.randn(2, 77, 2048)
        add_text_embeds = torch.randn(2, 1280)
        add_time_ids = torch.randn(2, 6)
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

        ctx = DenoisingContext(
            unet=unet,
            latent_model_input=latent,
            timestep=timestep,
            noise_pred=None,
            current_step=5,
            total_steps=25,
            progress=0.2,
            prompt_embeds=prompt_embeds,
            add_text_embeds=add_text_embeds,
            add_time_ids=add_time_ids,
            cross_attention_kwargs={"scale": 1.0},
            do_cfg=True,
            guidance_scale=7.5,
            alphas_cumprod=alphas_cumprod,
        )

        self.assertIs(ctx.unet, unet)
        self.assertTrue(torch.equal(ctx.latent_model_input, latent))
        self.assertTrue(torch.equal(ctx.timestep, timestep))
        self.assertIsNone(ctx.noise_pred)
        self.assertEqual(ctx.current_step, 5)
        self.assertEqual(ctx.total_steps, 25)
        self.assertAlmostEqual(ctx.progress, 0.2)
        self.assertTrue(torch.equal(ctx.prompt_embeds, prompt_embeds))
        self.assertTrue(torch.equal(ctx.add_text_embeds, add_text_embeds))
        self.assertTrue(torch.equal(ctx.add_time_ids, add_time_ids))
        self.assertEqual(ctx.cross_attention_kwargs, {"scale": 1.0})
        self.assertTrue(ctx.do_cfg)
        self.assertAlmostEqual(ctx.guidance_scale, 7.5)
        self.assertTrue(torch.equal(ctx.alphas_cumprod, alphas_cumprod))

    def test_context_optional_fields_default_to_none(self):
        """Test that optional fields default to None."""
        unet = Mock()
        latent = torch.randn(2, 4, 64, 64)
        timestep = torch.tensor([500])
        prompt_embeds = torch.randn(2, 77, 2048)
        add_text_embeds = torch.randn(2, 1280)
        add_time_ids = torch.randn(2, 6)
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

        ctx = DenoisingContext(
            unet=unet,
            latent_model_input=latent,
            timestep=timestep,
            noise_pred=None,
            current_step=5,
            total_steps=25,
            progress=0.2,
            prompt_embeds=prompt_embeds,
            add_text_embeds=add_text_embeds,
            add_time_ids=add_time_ids,
            cross_attention_kwargs=None,
            do_cfg=True,
            guidance_scale=7.5,
            alphas_cumprod=alphas_cumprod,
        )

        self.assertIsNone(ctx.down_block_res_samples)
        self.assertIsNone(ctx.mid_block_res_sample)
        self.assertIsNone(ctx.inpaint_head_feature)
        self.assertIsNone(ctx.original_latent)

    def test_context_is_mutable(self):
        """Test that context fields can be modified in-place."""
        unet = Mock()
        latent = torch.randn(2, 4, 64, 64)
        timestep = torch.tensor([500])
        prompt_embeds = torch.randn(2, 77, 2048)
        add_text_embeds = torch.randn(2, 1280)
        add_time_ids = torch.randn(2, 6)
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

        ctx = DenoisingContext(
            unet=unet,
            latent_model_input=latent,
            timestep=timestep,
            noise_pred=None,
            current_step=5,
            total_steps=25,
            progress=0.2,
            prompt_embeds=prompt_embeds,
            add_text_embeds=add_text_embeds,
            add_time_ids=add_time_ids,
            cross_attention_kwargs=None,
            do_cfg=True,
            guidance_scale=7.5,
            alphas_cumprod=alphas_cumprod,
        )

        # Modify fields
        new_noise_pred = torch.randn(2, 4, 64, 64)
        ctx.noise_pred = new_noise_pred
        ctx.current_step = 10
        ctx.progress = 0.4

        self.assertTrue(torch.equal(ctx.noise_pred, new_noise_pred))
        self.assertEqual(ctx.current_step, 10)
        self.assertAlmostEqual(ctx.progress, 0.4)


class TestDenoisingHook(unittest.TestCase):
    """Test DenoisingHook base class behavior."""

    def _create_mock_context(self):
        """Helper to create a mock DenoisingContext."""
        unet = Mock()
        latent = torch.randn(2, 4, 64, 64)
        timestep = torch.tensor([500])
        prompt_embeds = torch.randn(2, 77, 2048)
        add_text_embeds = torch.randn(2, 1280)
        add_time_ids = torch.randn(2, 6)
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

        return DenoisingContext(
            unet=unet,
            latent_model_input=latent,
            timestep=timestep,
            noise_pred=None,
            current_step=5,
            total_steps=25,
            progress=0.2,
            prompt_embeds=prompt_embeds,
            add_text_embeds=add_text_embeds,
            add_time_ids=add_time_ids,
            cross_attention_kwargs=None,
            do_cfg=True,
            guidance_scale=7.5,
            alphas_cumprod=alphas_cumprod,
        )

    def test_default_on_pre_unet_returns_context_unchanged(self):
        """Test that default on_pre_unet returns context unchanged."""
        hook = DenoisingHook()
        ctx = self._create_mock_context()

        result = hook.on_pre_unet(ctx)

        self.assertIs(result, ctx)

    def test_default_on_post_unet_returns_context_unchanged(self):
        """Test that default on_post_unet returns context unchanged."""
        hook = DenoisingHook()
        ctx = self._create_mock_context()
        ctx.noise_pred = torch.randn(2, 4, 64, 64)

        result = hook.on_post_unet(ctx)

        self.assertIs(result, ctx)

    def test_default_on_post_cfg_returns_context_unchanged(self):
        """Test that default on_post_cfg returns context unchanged."""
        hook = DenoisingHook()
        ctx = self._create_mock_context()
        ctx.noise_pred = torch.randn(1, 4, 64, 64)

        result = hook.on_post_cfg(ctx)

        self.assertIs(result, ctx)

    def test_hook_has_default_name_and_priority(self):
        """Test that hook has default name and priority attributes."""
        hook = DenoisingHook()

        self.assertEqual(hook.name, "unnamed")
        self.assertEqual(hook.priority, 0)

    def test_hook_repr(self):
        """Test hook string representation."""
        hook = DenoisingHook()

        repr_str = repr(hook)

        self.assertIn("DenoisingHook", repr_str)
        self.assertIn("name='unnamed'", repr_str)
        self.assertIn("priority=0", repr_str)


class CustomHook(DenoisingHook):
    """Custom hook for testing lifecycle and modifications."""
    name = "custom"
    priority = 100

    def __init__(self):
        self.pre_unet_called = False
        self.post_unet_called = False
        self.post_cfg_called = False

    def on_pre_unet(self, ctx: DenoisingContext) -> DenoisingContext:
        self.pre_unet_called = True
        # Modify add_text_embeds by scaling
        ctx.add_text_embeds = ctx.add_text_embeds * 1.5
        return ctx

    def on_post_unet(self, ctx: DenoisingContext) -> DenoisingContext:
        self.post_unet_called = True
        # Modify noise_pred by adding noise
        if ctx.noise_pred is not None:
            ctx.noise_pred = ctx.noise_pred + torch.randn_like(ctx.noise_pred) * 0.01
        return ctx

    def on_post_cfg(self, ctx: DenoisingContext) -> DenoisingContext:
        self.post_cfg_called = True
        # Scale down noise_pred
        if ctx.noise_pred is not None:
            ctx.noise_pred = ctx.noise_pred * 0.9
        return ctx


class TestCustomHook(unittest.TestCase):
    """Test custom hook implementation."""

    def _create_mock_context(self):
        """Helper to create a mock DenoisingContext."""
        unet = Mock()
        latent = torch.randn(2, 4, 64, 64)
        timestep = torch.tensor([500])
        prompt_embeds = torch.randn(2, 77, 2048)
        add_text_embeds = torch.randn(2, 1280)
        add_time_ids = torch.randn(2, 6)
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

        return DenoisingContext(
            unet=unet,
            latent_model_input=latent,
            timestep=timestep,
            noise_pred=None,
            current_step=5,
            total_steps=25,
            progress=0.2,
            prompt_embeds=prompt_embeds,
            add_text_embeds=add_text_embeds,
            add_time_ids=add_time_ids,
            cross_attention_kwargs=None,
            do_cfg=True,
            guidance_scale=7.5,
            alphas_cumprod=alphas_cumprod,
        )

    def test_custom_hook_lifecycle_pre_unet(self):
        """Test that custom hook's on_pre_unet is called and modifies context."""
        hook = CustomHook()
        ctx = self._create_mock_context()
        original_embeds = ctx.add_text_embeds.clone()

        result = hook.on_pre_unet(ctx)

        self.assertTrue(hook.pre_unet_called)
        self.assertIs(result, ctx)
        # Check that add_text_embeds was scaled
        expected_embeds = original_embeds * 1.5
        self.assertTrue(torch.allclose(ctx.add_text_embeds, expected_embeds))

    def test_custom_hook_lifecycle_post_unet(self):
        """Test that custom hook's on_post_unet is called and modifies context."""
        hook = CustomHook()
        ctx = self._create_mock_context()
        ctx.noise_pred = torch.randn(2, 4, 64, 64)
        original_pred = ctx.noise_pred.clone()

        result = hook.on_post_unet(ctx)

        self.assertTrue(hook.post_unet_called)
        self.assertIs(result, ctx)
        # Check that noise_pred was modified (not equal to original)
        self.assertFalse(torch.equal(ctx.noise_pred, original_pred))

    def test_custom_hook_lifecycle_post_cfg(self):
        """Test that custom hook's on_post_cfg is called and modifies context."""
        hook = CustomHook()
        ctx = self._create_mock_context()
        ctx.noise_pred = torch.randn(1, 4, 64, 64)
        original_pred = ctx.noise_pred.clone()

        result = hook.on_post_cfg(ctx)

        self.assertTrue(hook.post_cfg_called)
        self.assertIs(result, ctx)
        # Check that noise_pred was scaled down
        expected_pred = original_pred * 0.9
        self.assertTrue(torch.allclose(ctx.noise_pred, expected_pred))

    def test_custom_hook_priority_and_name(self):
        """Test that custom hook has correct name and priority."""
        hook = CustomHook()

        self.assertEqual(hook.name, "custom")
        self.assertEqual(hook.priority, 100)


class TestHookPrioritySorting(unittest.TestCase):
    """Test hook priority sorting behavior."""

    def test_hooks_sorted_by_priority(self):
        """Test that hooks can be sorted by priority."""
        hook1 = DenoisingHook()
        hook1.name = "low"
        hook1.priority = 50

        hook2 = DenoisingHook()
        hook2.name = "high"
        hook2.priority = 10

        hook3 = DenoisingHook()
        hook3.name = "medium"
        hook3.priority = 30

        hooks = [hook1, hook2, hook3]
        sorted_hooks = sorted(hooks, key=lambda h: h.priority)

        self.assertEqual(sorted_hooks[0].name, "high")
        self.assertEqual(sorted_hooks[1].name, "medium")
        self.assertEqual(sorted_hooks[2].name, "low")

    def test_hooks_with_same_priority_maintain_order(self):
        """Test that hooks with same priority maintain their order."""
        hook1 = DenoisingHook()
        hook1.name = "first"
        hook1.priority = 10

        hook2 = DenoisingHook()
        hook2.name = "second"
        hook2.priority = 10

        hooks = [hook1, hook2]
        sorted_hooks = sorted(hooks, key=lambda h: h.priority)

        # Python's sort is stable, so original order is maintained
        self.assertEqual(sorted_hooks[0].name, "first")
        self.assertEqual(sorted_hooks[1].name, "second")


if __name__ == "__main__":
    unittest.main()
