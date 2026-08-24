"""
Tests for ADM Guidance Pipe and Hook (hook-based architecture).

Verifies:
- ADMGuidanceHook scales ONLY the width/height components of add_time_ids
  (Fooocus semantics) and never touches the pooled add_text_embeds
- ADMGuidancePipe registers hooks on the model
- Auto-tuning from model_type_info works correctly
- User-configured parameters override auto-tuning
"""
import pytest
import torch
import importlib.util
import sys
from unittest.mock import Mock, MagicMock

from src.pipelines.pipes.adm_guidance.sdxl.hook import ADMGuidanceHook
from src.pipelines.pipes.adm_guidance.sdxl.main import ADMGuidancePipe
from src.pipelines.pipes.generator.sdxl.denoising_hook import DenoisingContext
from src.pipelines.contracts import PipeInput, PipeOutput

# Load SDXLModelTypeInfo directly to avoid triggering the broken
# checkpoint_loader __init__.py import chain during refactoring
_spec = importlib.util.spec_from_file_location(
    "model_type_detector",
    "src/pipelines/pipes/checkpoint_loader/sdxl/model_type_detector.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["model_type_detector"] = _mod
_spec.loader.exec_module(_mod)
SDXLModelTypeInfo = _mod.SDXLModelTypeInfo


# --- Hook Tests ---


class TestADMGuidanceHook:
    """Tests for ADMGuidanceHook."""

    @pytest.fixture
    def hook(self):
        return ADMGuidanceHook(positive_scale=1.5, negative_scale=0.8, scaler_end=0.3)

    @pytest.fixture
    def make_ctx(self):
        """Factory to create a DenoisingContext with given step/total/timestep.

        `timestep` drives the ADM cutover (actual-timestep progress, not step
        index) - see should_apply_at_timestep. Defaults to 999.0 (progress≈0,
        i.e. the very first step of a 1000-step schedule).
        """
        def _make(current_step=0, total_steps=30, do_cfg=True, timestep=999.0):
            return DenoisingContext(
                unet=Mock(),
                latent_model_input=torch.randn(2, 4, 64, 64),
                timestep=torch.tensor([timestep]),
                noise_pred=None,
                current_step=current_step,
                total_steps=total_steps,
                progress=current_step / total_steps,
                prompt_embeds=torch.randn(2, 77, 2048),
                add_text_embeds=torch.randn(2, 1280),
                add_time_ids=torch.randn(2, 6),
                cross_attention_kwargs=None,
                do_cfg=do_cfg,
                guidance_scale=7.5,
                alphas_cumprod=torch.linspace(0.9999, 0.001, 1000),
            )
        return _make

    def test_hook_name_and_priority(self, hook):
        assert hook.name == "adm_guidance"
        assert hook.priority == 10

    def test_invalid_scaler_end_rejected(self):
        with pytest.raises(ValueError):
            ADMGuidanceHook(positive_scale=1.5, negative_scale=0.8, scaler_end=1.5)

    def test_never_scales_pooled_embeds(self, hook, make_ctx):
        """Pooled add_text_embeds must NEVER be modified — scaling them distorts
        text conditioning and causes burnt colors (the pre-fix behavior)."""
        ctx = make_ctx(current_step=2, total_steps=30)  # within scaler_end
        original_embeds = ctx.add_text_embeds.clone()

        result = hook.on_pre_unet(ctx)

        assert torch.equal(result.add_text_embeds, original_embeds)

    def test_scales_only_size_components_of_time_ids(self, hook, make_ctx):
        """During early steps with CFG, only the orig_height/orig_width
        components (indices 0-1) are scaled; crop and target stay unchanged."""
        ctx = make_ctx(current_step=2, total_steps=30)
        original_time_ids = ctx.add_time_ids.clone()

        result = hook.on_pre_unet(ctx)

        assert torch.allclose(result.add_time_ids[0, 0:2], original_time_ids[0, 0:2] * 0.8, atol=1e-5)
        assert torch.allclose(result.add_time_ids[1, 0:2], original_time_ids[1, 0:2] * 1.5, atol=1e-5)
        # crop coords + target size untouched
        assert torch.equal(result.add_time_ids[:, 2:], original_time_ids[:, 2:])

    def test_no_scaling_after_scaler_end(self, hook, make_ctx):
        """After scaler_end progress, embeds and time_ids should not be modified.

        Gating is driven by actual timestep progress (Fooocus `timed_adm`), not
        step index — timestep=499 on a 1000-step schedule gives progress≈0.5,
        past the 0.3 cutover, even though current_step is still early.
        """
        ctx = make_ctx(current_step=2, total_steps=30, timestep=499.0)  # progress≈0.5 > 0.3
        original_embeds = ctx.add_text_embeds.clone()
        original_time_ids = ctx.add_time_ids.clone()

        result = hook.on_pre_unet(ctx)

        assert torch.equal(result.add_text_embeds, original_embeds)
        assert torch.equal(result.add_time_ids, original_time_ids)

    def test_gating_tracks_timestep_not_step_index(self, hook, make_ctx):
        """A late step index with an early (high) timestep must still apply ADM —
        proves the gate is timestep-driven, not step-index-driven."""
        ctx = make_ctx(current_step=25, total_steps=30, timestep=990.0)  # progress≈0.01
        original_time_ids = ctx.add_time_ids.clone()

        result = hook.on_pre_unet(ctx)

        assert not torch.equal(result.add_time_ids, original_time_ids)

    def test_should_apply_at_timestep_boundary(self, hook):
        """should_apply_at_timestep should mirror Fooocus's strict `<` cutover."""
        alphas_cumprod = torch.linspace(0.9999, 0.001, 1000)
        # progress exactly at scaler_end (0.3) -> NOT applied (strict less-than)
        t_at_boundary = torch.tensor([(1.0 - hook.scaler_end) * 999.0])
        assert hook.should_apply_at_timestep(t_at_boundary, alphas_cumprod) is False
        # slightly higher t (earlier in the schedule, lower progress) -> applied
        t_past_boundary = torch.tensor([(1.0 - hook.scaler_end) * 999.0 + 1.0])
        assert hook.should_apply_at_timestep(t_past_boundary, alphas_cumprod) is True

    def test_positive_scale_applied_without_cfg(self, hook, make_ctx):
        """Without CFG only positive conditioning exists — its size components
        are scaled by positive_scale."""
        ctx = make_ctx(current_step=2, total_steps=30, do_cfg=False)
        original_time_ids = ctx.add_time_ids.clone()

        result = hook.on_pre_unet(ctx)

        assert torch.allclose(result.add_time_ids[:, 0:2], original_time_ids[:, 0:2] * 1.5, atol=1e-5)
        assert torch.equal(result.add_time_ids[:, 2:], original_time_ids[:, 2:])

    def test_missing_time_ids_is_noop(self, hook, make_ctx):
        ctx = make_ctx(current_step=2, total_steps=30)
        ctx.add_time_ids = None

        result = hook.on_pre_unet(ctx)

        assert result.add_time_ids is None

    def test_neutral_scales_are_noop(self, make_ctx):
        """Hook with neutral scales (1.0/1.0/0.0) should effectively be a no-op."""
        hook = ADMGuidanceHook(positive_scale=1.0, negative_scale=1.0, scaler_end=0.0)
        ctx = make_ctx(current_step=0, total_steps=30)
        original_embeds = ctx.add_text_embeds.clone()
        original_time_ids = ctx.add_time_ids.clone()

        result = hook.on_pre_unet(ctx)

        # scaler_end=0.0 means progress 0.0 <= 0.0 is True, so scaling IS applied
        # but with 1.0 scales, results should be the same
        assert torch.allclose(result.add_text_embeds, original_embeds, atol=1e-5)
        assert torch.allclose(result.add_time_ids, original_time_ids, atol=1e-5)


# --- Pipe Tests ---


class TestADMGuidancePipe:
    """Tests for ADMGuidancePipe."""

    @pytest.fixture
    def default_pipe(self):
        config = ADMGuidancePipe.get_default_config()
        return ADMGuidancePipe(config)

    @pytest.fixture
    def mock_model(self):
        model = Mock(spec=["register_hook"])  # No model_type_info attribute
        model.register_hook = Mock()
        return model

    @pytest.fixture
    def anime_model_type_info(self):
        return SDXLModelTypeInfo(
            prediction_type="epsilon",
            uses_ztsnr=True,
            model_style="anime",
            recommended_adm_enabled=False,
            recommended_adm_positive_scale=1.0,
            recommended_adm_negative_scale=1.0,
            recommended_adm_scaler_end=0.0,
            recommended_guidance_rescale=0.0,
        )

    @pytest.fixture
    def realistic_model_type_info(self):
        return SDXLModelTypeInfo(
            prediction_type="epsilon",
            uses_ztsnr=False,
            model_style="realistic",
            recommended_adm_enabled=True,
            recommended_adm_positive_scale=1.5,
            recommended_adm_negative_scale=0.8,
            recommended_adm_scaler_end=0.3,
            recommended_guidance_rescale=0.0,
        )

    def test_default_config(self):
        config = ADMGuidancePipe.get_default_config()
        assert config["positive_scale"] == 1.5
        assert config["negative_scale"] == 0.8
        assert config["scaler_end"] == 0.3

    def test_pipe_metadata(self):
        pipe = ADMGuidancePipe(ADMGuidancePipe.get_default_config())
        assert pipe.name == "adm_guidance"
        assert pipe.description == "ADM Guidance enhancement for SDXL (Fooocus technique)"

    def test_inputs_spec(self):
        inputs = ADMGuidancePipe.inputs()
        assert len(inputs) == 1
        assert inputs[0].name == "model"

    def test_outputs_spec(self):
        outputs = ADMGuidancePipe.outputs()
        assert len(outputs) == 1
        assert outputs[0].name == "model"

    def test_configuration_spec(self):
        configs = ADMGuidancePipe.configuration()
        assert len(configs) == 3
        names = [c.name for c in configs]
        assert "positive_scale" in names
        assert "negative_scale" in names
        assert "scaler_end" in names

    def test_process_registers_hook(self, default_pipe, mock_model):
        """process() should register an ADMGuidanceHook on the model."""
        pipe_input = PipeInput(input={"model": mock_model})

        result = default_pipe.process(pipe_input, Mock())

        mock_model.register_hook.assert_called_once()
        call_args = mock_model.register_hook.call_args
        assert call_args[0][0] == "adm_guidance"
        assert isinstance(call_args[0][1], ADMGuidanceHook)
        assert result.output["model"] is mock_model

    def test_process_with_defaults(self, default_pipe, mock_model):
        """With defaults and no model_type_info, should use default scales."""
        pipe_input = PipeInput(input={"model": mock_model})

        default_pipe.process(pipe_input, Mock())

        hook = mock_model.register_hook.call_args[0][1]
        assert hook.positive_scale == 1.5
        assert hook.negative_scale == 0.8
        assert hook.scaler_end == 0.3

    def test_auto_tune_anime_model(self, default_pipe, anime_model_type_info):
        """Anime model should auto-tune to neutral scales."""
        model = Mock()
        model.register_hook = Mock()
        model.model_type_info = anime_model_type_info

        pipe_input = PipeInput(input={"model": model})
        default_pipe.process(pipe_input, Mock())

        hook = model.register_hook.call_args[0][1]
        assert hook.positive_scale == 1.0
        assert hook.negative_scale == 1.0
        assert hook.scaler_end == 0.0

    def test_auto_tune_realistic_model(self, default_pipe, realistic_model_type_info):
        """Realistic model should keep Fooocus defaults."""
        model = Mock()
        model.register_hook = Mock()
        model.model_type_info = realistic_model_type_info

        pipe_input = PipeInput(input={"model": model})
        default_pipe.process(pipe_input, Mock())

        hook = model.register_hook.call_args[0][1]
        assert hook.positive_scale == 1.5
        assert hook.negative_scale == 0.8
        assert hook.scaler_end == 0.3

    def test_user_config_overrides_auto_tune(self, anime_model_type_info):
        """User-configured params should override auto-tuning."""
        config = ADMGuidancePipe.get_default_config()
        config["positive_scale"] = 1.8  # User changed this
        pipe = ADMGuidancePipe(config)

        model = Mock()
        model.register_hook = Mock()
        model.model_type_info = anime_model_type_info

        pipe_input = PipeInput(input={"model": model})
        pipe.process(pipe_input, Mock())

        hook = model.register_hook.call_args[0][1]
        # Should use user config, not anime auto-tune
        assert hook.positive_scale == 1.8
        assert hook.negative_scale == 0.8
        assert hook.scaler_end == 0.3

    def test_no_model_type_info_uses_defaults(self, default_pipe):
        """Model without model_type_info should use config defaults."""
        model = Mock(spec=[])  # No model_type_info attribute
        model.register_hook = Mock()

        pipe_input = PipeInput(input={"model": model})
        default_pipe.process(pipe_input, Mock())

        hook = model.register_hook.call_args[0][1]
        assert hook.positive_scale == 1.5
        assert hook.negative_scale == 0.8
        assert hook.scaler_end == 0.3
