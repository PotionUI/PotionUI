"""
Tests for SAG pipe and hook (ComfyUI-parity rework).

Tests cover:
- SAGPipe registers hook on model with the new sag_threshold config key
- build_blur_map / gaussian_blur_2d math against small hand-checkable tensors
- eps_to_x0 / x0_to_eps round-trip
- SAGHook lifecycle: on_pre_unet installs the recording processor, on_post_unet
  restores it and stashes sag_state, on_post_cfg consumes it and mixes the
  correction after CFG
- No-op behavior when disabled (scale=0.0) or CFG is off
"""
import unittest
from unittest.mock import Mock, MagicMock
import torch

from src.pipelines.pipes.sag.sdxl.main import SAGPipe
from src.pipelines.pipes.sag.sdxl.hook import (
    SAGHook,
    _SAGRecordingAttnProcessor,
    build_blur_map,
    gaussian_blur_2d,
    eps_to_x0,
    x0_to_eps,
    get_alpha_for_timestep,
)
from src.pipelines.pipes.generator.sdxl.denoising_hook import DenoisingContext
from src.pipelines.contracts import PipeInput


class TestSAGPipe(unittest.TestCase):
    """Test SAGPipe behavior."""

    def _create_mock_model(self):
        model = Mock()
        model.denoising_hooks = {}
        model.register_hook = lambda name, hook: model.denoising_hooks.__setitem__(name, hook)
        return model

    def _create_pipe_input(self, model):
        pipe_input = Mock(spec=PipeInput)
        pipe_input.input = {"model": model}
        return pipe_input

    def test_pipe_registers_hook_on_model(self):
        model = self._create_mock_model()
        pipe_input = self._create_pipe_input(model)

        pipe = SAGPipe(config={})
        output = pipe.process(pipe_input, Mock())

        self.assertIn("sag", model.denoising_hooks)
        self.assertIsInstance(model.denoising_hooks["sag"], SAGHook)
        self.assertEqual(output.output["model"], model)

    def test_pipe_uses_default_config(self):
        model = self._create_mock_model()
        pipe_input = self._create_pipe_input(model)

        pipe = SAGPipe(config={})
        pipe.process(pipe_input, Mock())

        hook = model.denoising_hooks["sag"]
        self.assertAlmostEqual(hook.scale, 0.75)
        self.assertAlmostEqual(hook.sigma, 2.0)
        self.assertAlmostEqual(hook.sag_threshold, 1.0)

    def test_pipe_uses_user_config(self):
        model = self._create_mock_model()
        pipe_input = self._create_pipe_input(model)

        config = {
            "scale": 0.85,
            "sigma": 1.5,
            "sag_threshold": 0.6,
        }
        pipe = SAGPipe(config=config)
        pipe.process(pipe_input, Mock())

        hook = model.denoising_hooks["sag"]
        self.assertAlmostEqual(hook.scale, 0.85)
        self.assertAlmostEqual(hook.sigma, 1.5)
        self.assertAlmostEqual(hook.sag_threshold, 0.6)

    def test_pipe_has_correct_metadata(self):
        pipe = SAGPipe(config={})

        self.assertEqual(pipe.name, "sag")
        self.assertEqual(pipe.description, "Self-Attention Guidance for SDXL (enhanced detail, ~15-20% overhead)")

    def test_pipe_inputs_spec(self):
        inputs = SAGPipe.inputs()

        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0].name, "model")
        self.assertTrue(inputs[0].required)

    def test_pipe_outputs_spec(self):
        outputs = SAGPipe.outputs()

        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].name, "model")

    def test_pipe_configuration_spec(self):
        configs = SAGPipe.configuration()

        self.assertEqual(len(configs), 3)
        config_names = [c.name for c in configs]
        self.assertIn("scale", config_names)
        self.assertIn("sigma", config_names)
        self.assertIn("sag_threshold", config_names)
        # the old step-progress "threshold" knob must be gone
        self.assertNotIn("threshold", config_names)


class TestGaussianBlur2d(unittest.TestCase):
    """Math checks for the Gaussian blur used to build the degraded x0."""

    def test_blur_preserves_constant_image(self):
        """Blurring a constant field must return (approximately) the same
        constant, since a normalized kernel sums to 1 and reflect padding
        avoids edge darkening."""
        img = torch.full((1, 3, 16, 16), 2.5)
        blurred = gaussian_blur_2d(img, kernel_size=9, sigma=2.0)
        self.assertTrue(torch.allclose(blurred, img, atol=1e-4))

    def test_blur_smooths_impulse(self):
        """An impulse in the center should spread energy to its neighbors and
        reduce the peak value."""
        img = torch.zeros(1, 1, 17, 17)
        img[0, 0, 8, 8] = 1.0
        blurred = gaussian_blur_2d(img, kernel_size=9, sigma=2.0)

        self.assertLess(blurred[0, 0, 8, 8].item(), 1.0)
        self.assertGreater(blurred[0, 0, 8, 9].item(), 0.0)
        self.assertGreater(blurred[0, 0, 7, 8].item(), 0.0)

    def test_output_shape_matches_input(self):
        img = torch.randn(2, 4, 32, 32)
        blurred = gaussian_blur_2d(img, kernel_size=9, sigma=1.0)
        self.assertEqual(blurred.shape, img.shape)


class TestBuildBlurMap(unittest.TestCase):
    """Math checks for attention -> mask -> blur (build_blur_map)."""

    def test_high_attention_regions_are_blurred_low_are_not(self):
        """A hand-built attention map with one clearly "hot" key position and
        the rest cold should blur only the hot position's mapped region."""
        b, heads = 1, 4
        lh, lw = 8, 8
        seq = lh * lw  # 64

        # attn shape [batch*heads, seq_q, seq_k]; make every query position
        # agree that key position 0 is "hot" (large uniform score) and all
        # others are cold (near zero), so GAP over queries/heads clearly
        # separates the mask.
        attn = torch.zeros(b * heads, seq, seq)
        attn[:, :, 0] = 5.0  # hot key position -> exceeds any reasonable threshold
        attn[:, :, 1:] = 0.01

        x0 = torch.zeros(b, 3, lh, lw)
        x0[:, :, 0, 0] = 10.0  # distinctive value at the hot position

        degraded = build_blur_map(x0, attn, sigma=1.0, threshold=1.0)

        # The hot position should have been blurred away from its original
        # sharp value (mixed with its zero neighbors).
        self.assertLess(degraded[0, 0, 0, 0].item(), x0[0, 0, 0, 0].item())
        # A position with no attention mass should be left untouched (mask=0).
        self.assertTrue(torch.allclose(degraded[0, :, 4, 4], x0[0, :, 4, 4], atol=1e-5))

    def test_output_shape_matches_x0(self):
        b, heads = 2, 8
        lh, lw = 8, 8
        seq = lh * lw
        attn = torch.rand(b * heads, seq, seq)
        x0 = torch.randn(b, 4, lh, lw)

        degraded = build_blur_map(x0, attn, sigma=2.0, threshold=1.0)
        self.assertEqual(degraded.shape, x0.shape)

    def test_threshold_above_all_scores_is_noop(self):
        """If the threshold is higher than any attention magnitude, the mask
        is all-zero and the output must equal x0 exactly."""
        b, heads = 1, 2
        lh, lw = 16, 16
        seq = lh * lw
        attn = torch.full((b * heads, seq, seq), 0.001)
        x0 = torch.randn(b, 3, lh, lw)

        degraded = build_blur_map(x0, attn, sigma=1.0, threshold=1000.0)
        self.assertTrue(torch.allclose(degraded, x0, atol=1e-6))


class TestEpsX0Conversion(unittest.TestCase):
    """Round-trip checks for the eps<->x0 helpers used to translate the SAG
    correction into this wrapper's eps-space (same formulas as
    AnisotropicSharpness.apply_during_denoising)."""

    def test_round_trip_recovers_eps(self):
        latent = torch.randn(2, 4, 8, 8)
        eps = torch.randn(2, 4, 8, 8)
        alpha_t = torch.tensor(0.42)

        x0 = eps_to_x0(latent, eps, alpha_t)
        recovered_eps = x0_to_eps(latent, x0, alpha_t)

        self.assertTrue(torch.allclose(recovered_eps, eps, atol=1e-5))

    def test_get_alpha_for_timestep_indexes_correctly(self):
        alphas_cumprod = torch.linspace(0.9999, 0.001, 1000)
        t = torch.tensor([500.0])
        alpha = get_alpha_for_timestep(t, alphas_cumprod)
        self.assertAlmostEqual(alpha.item(), alphas_cumprod[500].item(), places=5)


def _make_unet_with_target_attn(batch=2, seq_len=16, heads=2, dim=8):
    """Builds a minimal mock UNet exposing mid_block.attentions[0]
    .transformer_blocks[0].attn1 with a settable/gettable `processor`, plus a
    callable UNet forward returning a fixed-shape noise prediction."""
    unet = MagicMock()
    attn1 = MagicMock()
    attn1.processor = Mock(name="original_processor")

    def set_processor(p):
        attn1.processor = p
    attn1.set_processor = Mock(side_effect=set_processor)

    unet.mid_block.attentions = [MagicMock()]
    unet.mid_block.attentions[0].transformer_blocks = [MagicMock()]
    unet.mid_block.attentions[0].transformer_blocks[0].attn1 = attn1
    unet.parameters = Mock(return_value=iter([torch.zeros(1, dtype=torch.float32)]))
    return unet, attn1


class TestSAGHookLifecycle(unittest.TestCase):
    """Behavioral tests for the new on_pre_unet / on_post_unet / on_post_cfg
    lifecycle (attention recording -> blur-map -> re-noise -> extra forward
    -> post-CFG mix)."""

    def _make_ctx(self, do_cfg=True, batch=2, latent_size=8, unet=None):
        full_batch = batch * 2 if do_cfg else batch
        latent = torch.randn(full_batch, 4, latent_size, latent_size)
        return DenoisingContext(
            unet=unet if unet is not None else Mock(),
            latent_model_input=latent,
            timestep=torch.full((full_batch,), 500.0),
            noise_pred=torch.randn(full_batch, 4, latent_size, latent_size),
            current_step=5,
            total_steps=25,
            progress=0.2,
            prompt_embeds=torch.randn(full_batch, 77, 2048),
            add_text_embeds=torch.randn(full_batch, 1280),
            add_time_ids=torch.randn(full_batch, 6),
            cross_attention_kwargs=None,
            do_cfg=do_cfg,
            guidance_scale=7.5,
            alphas_cumprod=torch.linspace(0.9999, 0.001, 1000),
            original_latent=latent[:batch] if do_cfg else latent,
        )

    def test_hook_metadata(self):
        hook = SAGHook(scale=0.75, sigma=2.0, sag_threshold=1.0)
        self.assertEqual(hook.name, "sag")
        self.assertEqual(hook.priority, 45)

    def test_disabled_when_scale_zero(self):
        """scale=0.0 must be a complete no-op across the whole lifecycle."""
        hook = SAGHook(scale=0.0, sigma=2.0, sag_threshold=1.0)
        unet, attn1 = _make_unet_with_target_attn()
        ctx = self._make_ctx(unet=unet)

        ctx = hook.on_pre_unet(ctx)
        attn1.set_processor.assert_not_called()

        original_noise_pred = ctx.noise_pred.clone()
        ctx = hook.on_post_unet(ctx)
        self.assertIsNone(ctx.sag_state)

        ctx = hook.on_post_cfg(ctx)
        self.assertTrue(torch.equal(ctx.noise_pred, original_noise_pred))

    def test_disabled_when_cfg_off(self):
        hook = SAGHook(scale=0.75, sigma=2.0, sag_threshold=1.0)
        unet, attn1 = _make_unet_with_target_attn()
        ctx = self._make_ctx(do_cfg=False, unet=unet)

        ctx = hook.on_pre_unet(ctx)
        attn1.set_processor.assert_not_called()

    def test_on_pre_unet_installs_recording_processor(self):
        hook = SAGHook(scale=0.75, sigma=2.0, sag_threshold=1.0)
        unet, attn1 = _make_unet_with_target_attn()
        ctx = self._make_ctx(unet=unet)

        hook.on_pre_unet(ctx)

        self.assertIsInstance(attn1.processor, _SAGRecordingAttnProcessor)
        self.assertEqual(attn1.processor.uncond_batch, ctx.latent_model_input.shape[0] // 2)

    def test_on_post_unet_restores_processor_and_stashes_state(self):
        hook = SAGHook(scale=0.75, sigma=2.0, sag_threshold=1.0)
        unet, attn1 = _make_unet_with_target_attn()
        ctx = self._make_ctx(unet=unet, latent_size=8)

        hook.on_pre_unet(ctx)
        original_processor = hook._original_processor
        # Simulate the main UNet forward having recorded attention probs.
        heads, seq = 2, 64  # 8*8
        batch = ctx.noise_pred.shape[0] // 2
        hook._processor.recorded = torch.rand(heads * batch, seq, seq)

        hook.on_post_unet(ctx)

        self.assertIs(attn1.processor, original_processor)
        self.assertIsNotNone(ctx.sag_state)
        self.assertIn("uncond_eps", ctx.sag_state)
        self.assertIn("attn", ctx.sag_state)
        self.assertTrue(torch.equal(ctx.sag_state["uncond_eps"], ctx.noise_pred[:batch]))

    def test_on_post_unet_skips_when_too_small(self):
        """Matches ComfyUI's `min(cfg_result.shape[2:]) <= 4` guard."""
        hook = SAGHook(scale=0.75, sigma=2.0, sag_threshold=1.0)
        unet, attn1 = _make_unet_with_target_attn()
        ctx = self._make_ctx(unet=unet, latent_size=4)

        hook.on_pre_unet(ctx)
        hook._processor.recorded = torch.rand(2 * 1, 16, 16)

        hook.on_post_unet(ctx)

        self.assertIsNone(ctx.sag_state)

    def test_on_post_cfg_noop_without_sag_state(self):
        hook = SAGHook(scale=0.75, sigma=2.0, sag_threshold=1.0)
        ctx = self._make_ctx()
        ctx.sag_state = None
        original = ctx.noise_pred.clone()

        hook.on_post_cfg(ctx)

        self.assertTrue(torch.equal(ctx.noise_pred, original))

    def test_on_post_cfg_mixes_correction_and_calls_unet_once(self):
        hook = SAGHook(scale=0.75, sigma=2.0, sag_threshold=1.0)
        batch, latent_size = 2, 8
        unet, attn1 = _make_unet_with_target_attn(batch=batch)
        ctx = self._make_ctx(unet=unet, batch=batch, latent_size=latent_size)
        # By the time on_post_cfg runs, noise_pred is already the CFG-combined
        # prediction - true batch size, not the CFG-doubled batch.
        ctx.noise_pred = torch.randn(batch, 4, latent_size, latent_size)

        heads, seq = 2, latent_size * latent_size
        ctx.sag_state = {
            "uncond_eps": torch.randn(batch, 4, latent_size, latent_size),
            "attn": torch.rand(heads * batch, seq, seq),
        }

        sag_forward_output = torch.randn(batch, 4, latent_size, latent_size)
        unet.return_value = (sag_forward_output,)
        original_cfg_pred = ctx.noise_pred.clone()

        result = hook.on_post_cfg(ctx)

        unet.assert_called_once()
        call_kwargs = unet.call_args[1]
        self.assertIn("encoder_hidden_states", call_kwargs)
        self.assertIn("added_cond_kwargs", call_kwargs)
        self.assertEqual(call_kwargs["encoder_hidden_states"].shape[0], batch)

        self.assertIs(result, ctx)
        self.assertIsNone(ctx.sag_state)
        # Correction should generally change the CFG prediction (near-zero
        # probability of an exact match with random tensors).
        self.assertFalse(torch.equal(ctx.noise_pred, original_cfg_pred))

    def test_on_post_cfg_scale_zero_leaves_prediction_effectively_unchanged(self):
        """With sag_state present but scale explicitly forced to 0 after
        construction, the (degraded - sag) correction should be nulled out."""
        hook = SAGHook(scale=0.75, sigma=2.0, sag_threshold=1.0)
        batch, latent_size = 1, 8
        unet, attn1 = _make_unet_with_target_attn(batch=batch)
        ctx = self._make_ctx(unet=unet, batch=batch, latent_size=latent_size)

        heads, seq = 2, latent_size * latent_size
        uncond_eps = torch.randn(batch, 4, latent_size, latent_size)
        ctx.sag_state = {
            "uncond_eps": uncond_eps,
            "attn": torch.rand(heads * batch, seq, seq),
        }
        # Make the "extra forward" degenerate: return exactly uncond_eps so
        # degraded_x0 == sag_x0 only when attention magnitude never exceeds
        # threshold (mask all-zero) - here we instead just verify the hook
        # short-circuits entirely when scale is 0 at call time.
        hook.scale = 0.0
        original_cfg_pred = ctx.noise_pred.clone()

        hook.on_post_cfg(ctx)

        unet.assert_not_called()
        self.assertTrue(torch.equal(ctx.noise_pred, original_cfg_pred))


if __name__ == "__main__":
    unittest.main()
