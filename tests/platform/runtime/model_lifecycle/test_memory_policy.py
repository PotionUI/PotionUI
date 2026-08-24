from src.platform.runtime.model_lifecycle.memory_policy import MemoryPolicy


class TestOffloadStrategyTiers:
    def test_under_8gb_is_sequential(self):
        assert MemoryPolicy(4.0).get_offload_strategy() == "sequential"
        assert MemoryPolicy(7.99).get_offload_strategy() == "sequential"

    def test_8_to_12gb_is_model_offload(self):
        assert MemoryPolicy(8.0).get_offload_strategy() == "model"
        assert MemoryPolicy(11.99).get_offload_strategy() == "model"

    def test_12gb_and_above_is_no_offload(self):
        assert MemoryPolicy(12.0).get_offload_strategy() == "none"
        assert MemoryPolicy(24.0).get_offload_strategy() == "none"


class TestAttentionSlicingTiers:
    def test_under_8gb_is_max(self):
        assert MemoryPolicy(4.0).get_attention_slicing() == "max"

    def test_8_to_12gb_is_auto(self):
        assert MemoryPolicy(10.0).get_attention_slicing() == "auto"

    def test_12gb_and_above_is_none(self):
        assert MemoryPolicy(16.0).get_attention_slicing() == "none"


class TestVaeTiers:
    def test_vae_slicing_enabled_under_16gb(self):
        assert MemoryPolicy(8.0).should_enable_vae_slicing() is True
        assert MemoryPolicy(15.99).should_enable_vae_slicing() is True

    def test_vae_slicing_disabled_at_16gb_and_above(self):
        assert MemoryPolicy(16.0).should_enable_vae_slicing() is False
        assert MemoryPolicy(24.0).should_enable_vae_slicing() is False

    def test_vae_tiling_enabled_under_12gb(self):
        assert MemoryPolicy(8.0).should_enable_vae_tiling() is True

    def test_vae_tiling_disabled_at_12gb_and_above(self):
        assert MemoryPolicy(12.0).should_enable_vae_tiling() is False
        assert MemoryPolicy(24.0).should_enable_vae_tiling() is False


class TestMemoryStrategyTiers:
    def test_conservative_under_12gb(self):
        assert MemoryPolicy(4.0).get_memory_strategy() == "conservative"
        assert MemoryPolicy(11.99).get_memory_strategy() == "conservative"

    def test_balanced_12_to_24gb(self):
        assert MemoryPolicy(12.0).get_memory_strategy() == "balanced"
        assert MemoryPolicy(23.99).get_memory_strategy() == "balanced"

    def test_full_vram_at_24gb_and_above(self):
        assert MemoryPolicy(24.0).get_memory_strategy() == "full_vram"
        assert MemoryPolicy(48.0).get_memory_strategy() == "full_vram"


class TestShouldUseCpuOffload:
    def test_conservative_always_offloads(self):
        assert MemoryPolicy(4.0).should_use_cpu_offload("sdxl") is True

    def test_full_vram_never_offloads(self):
        assert MemoryPolicy(24.0).should_use_cpu_offload("sdxl") is False

    def test_balanced_offloads_when_model_uses_majority_of_budget(self):
        # flux base cost 24.0GB; at 12GB budget that's > 50% -> offload
        assert MemoryPolicy(12.0).should_use_cpu_offload("flux") is True

    def test_balanced_does_not_offload_when_model_fits_comfortably(self):
        # sdxl base cost 4.0GB; at 20GB budget that's well under 50% -> no offload
        assert MemoryPolicy(20.0).should_use_cpu_offload("sdxl") is False

    def test_flux_family_sizes_share_gb_unit_with_krea2_and_ltx2(self):
        # flux2/flux_klein are 9B-parameter DiTs; bf16 GB (~18) must be in the
        # same unit as krea2/ltx2's already-GB entries (~26/27), not the raw
        # params-in-billions figure (9) that undercounts by ~2x.
        assert MemoryPolicy(20.0).should_use_cpu_offload("flux2") is True
        assert MemoryPolicy(20.0).should_use_cpu_offload("flux_klein") is True


class TestTierSnapshots:
    """Snapshot the full decision set at representative VRAM budgets."""

    def test_8gb_tier_snapshot(self):
        p = MemoryPolicy(8.0)
        assert p.get_offload_strategy() == "model"
        assert p.get_attention_slicing() == "auto"
        assert p.should_enable_vae_slicing() is True
        assert p.should_enable_vae_tiling() is True

    def test_12gb_tier_snapshot(self):
        p = MemoryPolicy(12.0)
        assert p.get_offload_strategy() == "none"
        assert p.get_attention_slicing() == "none"
        assert p.should_enable_vae_slicing() is True
        assert p.should_enable_vae_tiling() is False

    def test_16gb_tier_snapshot(self):
        p = MemoryPolicy(16.0)
        assert p.get_offload_strategy() == "none"
        assert p.get_attention_slicing() == "none"
        assert p.should_enable_vae_slicing() is False
        assert p.should_enable_vae_tiling() is False

    def test_24gb_tier_snapshot(self):
        p = MemoryPolicy(24.0)
        assert p.get_offload_strategy() == "none"
        assert p.get_attention_slicing() == "none"
        assert p.should_enable_vae_slicing() is False
        assert p.should_enable_vae_tiling() is False
        assert p.get_memory_strategy() == "full_vram"
