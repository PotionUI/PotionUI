"""Tiny-weights behavioral tests for the YuE2 backbone: state-dict key
naming, AR prefill/step KV-cache equivalence, and the NAR velocity path's
output shape.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.yue2.config import YuE2Config
from src.platform.runtime.native.arch.yue2.model import YuE2Model
from vendor.gpl.comfyui.ops import disable_weight_init


def _tiny_config(latent_dim: int = 4, max_latent_frames: int = 32) -> YuE2Config:
    return YuE2Config(
        hidden_size=16, num_hidden_layers=2, num_attention_heads=2, num_key_value_heads=1,
        head_dim=8, intermediate_size=24, vocab_size=64, rope_theta=10000.0,
        max_position_embeddings=128, latent_dim=latent_dim, max_latent_frames=max_latent_frames,
        timestep_shift=1.0,
    )


def _randomize(module: torch.nn.Module, seed: int = 0) -> None:
    g = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for p in module.parameters():
            p.copy_(torch.randn(p.shape, generator=g) * 0.5)


def _build_model(seed: int = 0, latent_dim: int = 4, dtype: torch.dtype = torch.float32) -> YuE2Model:
    cfg = _tiny_config(latent_dim=latent_dim)
    model = YuE2Model(cfg, disable_weight_init, dtype=dtype)
    _randomize(model, seed)
    model.post_load()
    model.eval()
    return model


class TestMetaConstruction:
    def test_post_load_moves_every_derived_buffer_off_meta(self):
        with torch.device("meta"):
            meta_model = YuE2Model(_tiny_config(), disable_weight_init, dtype=torch.float32)
        cpu_model = _build_model()
        assert meta_model.latent_pos_embed.pe.device.type == "meta"

        meta_model.requires_grad_(False)
        meta_model.load_state_dict(cpu_model.state_dict(), strict=False, assign=True)
        meta_model.post_load()

        for name, tensor in list(meta_model.named_parameters()) + list(meta_model.named_buffers()):
            assert tensor.device.type != "meta", name
        torch.testing.assert_close(meta_model.latent_pos_embed.pe, cpu_model.latent_pos_embed.pe)


class TestStateDictKeys:
    def test_matches_expected_checkpoint_naming(self):
        cfg = YuE2Config(
            hidden_size=8, num_hidden_layers=1, num_attention_heads=2, num_key_value_heads=1,
            head_dim=4, intermediate_size=12, vocab_size=32, latent_dim=4, max_latent_frames=8,
        )
        model = YuE2Model(cfg, disable_weight_init)
        expected = {
            "model.embed_tokens.weight",
            "model.norm.weight",
            "lm_head.weight",
            "llm2vae.weight", "llm2vae.bias",
            "vae2llm.weight", "vae2llm.bias",
            "time_embedder.mlp.0.weight", "time_embedder.mlp.0.bias",
            "time_embedder.mlp.2.weight", "time_embedder.mlp.2.bias",
        }
        for branch in ("self_attn", "nar_self_attn"):
            for proj in ("q_proj", "k_proj", "v_proj", "o_proj"):
                expected.add(f"model.layers.0.{branch}.{proj}.weight")
            expected.add(f"model.layers.0.{branch}.q_norm.weight")
            expected.add(f"model.layers.0.{branch}.k_norm.weight")
        for mlp, prefix in (("mlp", "post_attention_layernorm"), ("nar_mlp", "nar_pre_mlp_layernorm")):
            for proj in ("gate_proj", "up_proj", "down_proj"):
                expected.add(f"model.layers.0.{mlp}.{proj}.weight")
            expected.add(f"model.layers.0.{prefix}.weight")
        expected.add("model.layers.0.input_layernorm.weight")
        expected.add("model.layers.0.nar_input_layernorm.weight")
        assert set(model.state_dict().keys()) == expected

    def test_non_persistent_buffers_are_excluded(self):
        cfg = _tiny_config()
        model = YuE2Model(cfg, disable_weight_init)
        keys = set(model.state_dict().keys())
        assert not any("inv_freq" in k for k in keys)
        assert not any("latent_pos_embed" in k for k in keys)


class TestArPrefillStepEquivalence:
    def test_incremental_step_matches_full_prefill(self):
        model = _build_model(seed=1)
        torch.manual_seed(2)
        prompt_len, extra = 5, 4
        total = prompt_len + extra
        ids = torch.randint(0, 64, (1, total))

        cache_full = model.new_kv_cache(max_len=total, batch=1, dtype=torch.float32)
        with torch.inference_mode():
            hidden_full = model.prefill(ids, cache_full)

        cache_inc = model.new_kv_cache(max_len=total, batch=1, dtype=torch.float32)
        with torch.inference_mode():
            model.prefill(ids[:, :prompt_len], cache_inc)
            steps = []
            for i in range(prompt_len, total):
                steps.append(model.step(ids[:, i:i + 1], cache_inc))
            hidden_inc = torch.cat(steps, dim=1)

        torch.testing.assert_close(hidden_inc, hidden_full[:, prompt_len:], atol=1e-4, rtol=1e-4)

    def test_prefill_output_shape(self):
        model = _build_model(seed=3)
        ids = torch.randint(0, 64, (1, 7))
        cache = model.new_kv_cache(max_len=7, batch=1)
        hidden = model.prefill(ids, cache)
        assert hidden.shape == (1, 7, model.cfg.hidden_size)
        logits = model.lm_head_logits(hidden[:, -1, :])
        assert logits.shape == (1, model.cfg.vocab_size)


class TestNarVelocity:
    def test_output_shape_matches_latent_frame_count_and_dim(self):
        model = _build_model(seed=4, latent_dim=4)
        ar_ids = torch.randint(0, 64, (1, 6))
        num_frames = 5
        context = model.new_nar_context(ar_ids, num_latent_frames=num_frames)
        assert context.nar_length == num_frames + 2
        x_t = torch.randn(num_frames, 4)
        velocity = model.nar_velocity(context, x_t, t_value=0.3)
        assert velocity.shape == (num_frames, 4)

    def test_bf16_weights_take_fp32_noise_without_dtype_promotion(self):
        model = _build_model(seed=4, latent_dim=4, dtype=torch.bfloat16)
        ar_ids = torch.randint(0, 64, (1, 6))
        context = model.new_nar_context(ar_ids, num_latent_frames=5)
        velocity = model.nar_velocity(context, torch.randn(5, 4), t_value=0.3)
        assert velocity.dtype == torch.bfloat16
        assert velocity.shape == (5, 4)

    def test_rejects_mismatched_latent_frame_count(self):
        model = _build_model(seed=5, latent_dim=4)
        ar_ids = torch.randint(0, 64, (1, 6))
        context = model.new_nar_context(ar_ids, num_latent_frames=5)
        wrong = torch.randn(3, 4)
        try:
            model.nar_velocity(context, wrong, t_value=0.3)
        except ValueError:
            return
        raise AssertionError("expected ValueError for a mismatched latent frame count")

    def test_shift_timestep_is_identity_at_shift_one(self):
        model = _build_model(seed=6)
        t = torch.sigmoid(torch.tensor(0.42))
        shifted = model.shift_timestep(0.42, device=t.device, dtype=t.dtype)
        torch.testing.assert_close(shifted, t, atol=1e-5, rtol=1e-5)
