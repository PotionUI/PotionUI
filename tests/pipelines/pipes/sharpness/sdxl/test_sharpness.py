import pytest
import torch
from unittest.mock import MagicMock, patch

from src.pipelines.pipes.generator.sdxl.denoising_hook import DenoisingContext, DenoisingHook
from src.pipelines.pipes.sharpness.sdxl.hook import SharpnessHook
from src.pipelines.pipes.sharpness.sdxl.main import SharpnessPipe
from src.pipelines.contracts import PipeInput, PipeOutput, IOType


class TestSharpnessHook:
    """Tests for SharpnessHook."""

    def _make_context(self, do_cfg=True, batch_size=2, channels=4, height=64, width=64):
        """Create a minimal DenoisingContext for testing."""
        noise_pred = torch.randn(batch_size, channels, height, width)
        latent_model_input = torch.randn(batch_size, channels, height, width)
        alphas_cumprod = torch.linspace(0.9999, 0.001, 1000)

        return DenoisingContext(
            unet=MagicMock(),
            latent_model_input=latent_model_input,
            timestep=torch.tensor([500]),
            noise_pred=noise_pred,
            current_step=10,
            total_steps=25,
            progress=0.4,
            prompt_embeds=torch.randn(batch_size, 77, 2048),
            add_text_embeds=torch.randn(batch_size, 1280),
            add_time_ids=torch.randn(batch_size, 6),
            cross_attention_kwargs=None,
            do_cfg=do_cfg,
            guidance_scale=7.5,
            alphas_cumprod=alphas_cumprod,
            original_latent=torch.randn(batch_size // 2, channels, height, width) if do_cfg else None,
        )

    def test_inherits_from_denoising_hook(self):
        hook = SharpnessHook(strength=2.0)
        assert isinstance(hook, DenoisingHook)

    def test_name_and_priority(self):
        hook = SharpnessHook(strength=2.0)
        assert hook.name == "sharpness"
        assert hook.priority == 50

    def test_disabled_when_strength_zero(self):
        hook = SharpnessHook(strength=0.0)
        ctx = self._make_context()
        original_noise_pred = ctx.noise_pred.clone()
        result = hook.on_post_unet(ctx)
        assert torch.equal(result.noise_pred, original_noise_pred)

    def test_disabled_when_no_cfg(self):
        hook = SharpnessHook(strength=5.0)
        ctx = self._make_context(do_cfg=False)
        original_noise_pred = ctx.noise_pred.clone()
        result = hook.on_post_unet(ctx)
        assert torch.equal(result.noise_pred, original_noise_pred)

    @patch("src.pipelines.pipes.generator.sdxl.sharpness_filter.AnisotropicSharpness.apply_during_denoising")
    def test_applies_sharpness_with_cfg(self, mock_apply):
        """When CFG is enabled and strength > 0, sharpness should be applied to the text prediction."""
        hook = SharpnessHook(strength=5.0)
        ctx = self._make_context(do_cfg=True)

        # Make mock return a tensor of the right shape (half the batch for text pred)
        text_shape = ctx.noise_pred.chunk(2)[1].shape
        mock_apply.return_value = torch.randn(text_shape)

        result = hook.on_post_unet(ctx)

        mock_apply.assert_called_once()
        # noise_pred should be modified
        assert result.noise_pred.shape == ctx.latent_model_input.shape

    @patch("src.pipelines.pipes.generator.sdxl.sharpness_filter.AnisotropicSharpness.apply_during_denoising")
    def test_uses_original_latent_when_available(self, mock_apply):
        hook = SharpnessHook(strength=3.0)
        ctx = self._make_context(do_cfg=True)
        original_latent = torch.randn(1, 4, 64, 64)
        ctx.original_latent = original_latent

        text_shape = ctx.noise_pred.chunk(2)[1].shape
        mock_apply.return_value = torch.randn(text_shape)

        hook.on_post_unet(ctx)

        call_kwargs = mock_apply.call_args[1]
        assert torch.equal(call_kwargs["latent"], original_latent)

    @patch("src.pipelines.pipes.generator.sdxl.sharpness_filter.AnisotropicSharpness.apply_during_denoising")
    def test_falls_back_to_latent_model_input_slice(self, mock_apply):
        hook = SharpnessHook(strength=3.0)
        ctx = self._make_context(do_cfg=True)
        ctx.original_latent = None

        text_shape = ctx.noise_pred.chunk(2)[1].shape
        mock_apply.return_value = torch.randn(text_shape)

        hook.on_post_unet(ctx)

        call_kwargs = mock_apply.call_args[1]
        expected_latent = ctx.latent_model_input[:text_shape[0]]
        assert torch.equal(call_kwargs["latent"], expected_latent)

    def test_on_pre_unet_passthrough(self):
        hook = SharpnessHook(strength=5.0)
        ctx = self._make_context()
        result = hook.on_pre_unet(ctx)
        assert result is ctx

    def test_on_post_cfg_passthrough(self):
        hook = SharpnessHook(strength=5.0)
        ctx = self._make_context()
        result = hook.on_post_cfg(ctx)
        assert result is ctx


class TestSharpnessPipe:
    """Tests for SharpnessPipe."""

    def test_name_and_description(self):
        pipe = SharpnessPipe(config={"strength": 0.0})
        assert pipe.name == "sharpness"
        assert pipe.description == "Anisotropic sharpness enhancement for SDXL"

    def test_default_config(self):
        config = SharpnessPipe.get_default_config()
        assert config == {"strength": 0.0}

    def test_inputs_spec(self):
        inputs = SharpnessPipe.inputs()
        assert len(inputs) == 1
        assert inputs[0].name == "model"
        assert inputs[0].io_type == IOType.MODEL
        assert inputs[0].required is True

    def test_outputs_spec(self):
        outputs = SharpnessPipe.outputs()
        assert len(outputs) == 1
        assert outputs[0].name == "model"
        assert outputs[0].io_type == IOType.MODEL

    def test_configuration_spec(self):
        configs = SharpnessPipe.configuration()
        assert len(configs) == 1
        assert configs[0].name == "strength"
        assert configs[0].param_type == float
        assert configs[0].default == 0.0
        assert configs[0].min_value == 0.0
        assert configs[0].max_value == 30.0

    def test_process_registers_hook_when_strength_positive(self):
        model = MagicMock()
        pipe = SharpnessPipe(config={"strength": 5.0})
        pipe_input = PipeInput(input={"model": model})

        result = pipe.process(pipe_input, MagicMock())

        model.register_hook.assert_called_once()
        call_args = model.register_hook.call_args
        assert call_args[0][0] == "sharpness"
        assert isinstance(call_args[0][1], SharpnessHook)
        assert result.output["model"] is model

    def test_process_skips_hook_when_strength_zero(self):
        model = MagicMock()
        pipe = SharpnessPipe(config={"strength": 0.0})
        pipe_input = PipeInput(input={"model": model})

        result = pipe.process(pipe_input, MagicMock())

        model.register_hook.assert_not_called()
        assert result.output["model"] is model

    def test_process_returns_pipe_output(self):
        model = MagicMock()
        pipe = SharpnessPipe(config={"strength": 2.0})
        pipe_input = PipeInput(input={"model": model})

        result = pipe.process(pipe_input, MagicMock())

        assert isinstance(result, PipeOutput)
        assert "model" in result.output
