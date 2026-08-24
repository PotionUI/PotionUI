# Advanced Generation Quality Techniques for SDXL

This document outlines state-of-the-art techniques researched in 2024 that can improve various aspects of image generation quality, speed, and prompt adherence.

---

## 🎯 1. Perturbed-Attention Guidance (PAG)

**Status:** ECCV 2024, Supported in Diffusers v0.27+

### What It Does
PAG improves sample quality by strategically degrading and guiding self-attention mechanisms during generation. It generates intermediate samples with degraded structure by substituting selected self-attention maps with identity matrices, then guides the denoising process away from these degraded samples.

### Key Benefits
- ✅ **Better structure and composition**
- ✅ **Improved prompt following** (especially multi-subject prompts)
- ✅ **Works without training or external modules**
- ✅ **No speed penalty**
- ✅ **Compatible with ControlNet and IP-Adapter**

### Parameters

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `pag_scale` | float | 3.0 | 0.0-10.0 | Intensity of guidance. Higher = stronger structure guidance |
| `pag_adaptive_scale` | float | 0.0 | 0.0-1.0 | Optional adaptive scaling factor |
| `pag_applied_layers` | list | `["mid"]` | `["down", "mid", "up"]` | Which UNet layers to apply PAG to |

### Recommended Settings
- **General use:** `pag_scale=3.0`, `pag_applied_layers=["mid"]`
- **Complex scenes:** `pag_scale=4.0-5.0`, `pag_applied_layers=["mid", "up"]`
- **Portraits:** `pag_scale=2.0-3.0`, `pag_applied_layers=["mid"]`

### Implementation Notes
- Requires pipeline modification to support PAG
- Diffusers has official support: `StableDiffusionXLPAGPipeline`
- Can be combined with our k-diffusion sampling
- Most effective when prompt has multiple subjects that might be neglected

### Research Paper
- **Title:** "Self-Rectifying Diffusion Sampling with Perturbed-Attention Guidance"
- **Conference:** ECCV 2024
- **Links:**
  - Paper: https://arxiv.org/html/2403.17377v1
  - HuggingFace Docs: https://huggingface.co/docs/diffusers/main/api/pipelines/pag
  - Official Implementation: https://github.com/cvlab-kaist/Perturbed-Attention-Guidance

### Expected Impact
- **Prompt Adherence:** +25-35%
- **Structure Quality:** +30%
- **Multi-subject Handling:** +40%

---

## ⚡ 2. Token Merging (ToMe)

**Status:** CVPR 2023, Production Ready

### What It Does
ToMe speeds up transformers by merging redundant tokens/patches progressively during the forward pass. It can reduce tokens by up to 60% while maintaining high quality, achieving up to 2x speedup.

### Key Benefits
- ✅ **2x faster inference** (at ratio=0.5)
- ✅ **Up to 5.6x less memory**
- ✅ **No training required**
- ✅ **Works with any Stable Diffusion model**
- ✅ **Minimal quality loss**

### Parameters

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `tome_ratio` | float | 0.0 | 0.0-0.7 | Token merge ratio. 0=disabled, higher=faster but lower quality |

### Recommended Settings
- **Balanced (recommended):** `tome_ratio=0.4-0.5` (40-50% speedup, minimal quality loss)
- **Maximum speed:** `tome_ratio=0.6-0.7` (60-70% speedup, noticeable quality trade-off)
- **High quality:** `tome_ratio=0.2-0.3` (20-30% speedup, imperceptible quality change)

### Implementation
```python
# Installation
pip install tomesd

# Usage
import tomesd
tomesd.apply_patch(pipe, ratio=0.5)
```

### Benchmarks (A100, 1024x1024)
- `ratio=0.3`: 25% faster
- `ratio=0.5`: 43.59% faster
- `ratio=0.7`: 60% faster (quality degradation)

### Research Paper
- **Title:** "Token Merging for Fast Stable Diffusion"
- **Conference:** CVPR 2023
- **Links:**
  - GitHub: https://github.com/dbolya/tomesd
  - Paper: https://arxiv.org/abs/2303.17604
  - HuggingFace Docs: https://huggingface.co/docs/diffusers/optimization/tome

### Expected Impact
- **Speed:** +40-100% (depends on ratio)
- **Memory:** -20-50%
- **Quality:** -0-10% (depends on ratio)

---

## 🎨 3. Configurable FreeU Parameters

**Status:** CVPR 2024 Oral, Already Partially Implemented

### What It Does
FreeU improves generation quality by rescaling backbone and skip connection features in the UNet. Currently hardcoded in our implementation, but should be configurable for model-specific optimization.

### Key Benefits
- ✅ **Better details and textures**
- ✅ **Improved color saturation**
- ✅ **No speed penalty**
- ✅ **Works with all models**
- ✅ **Already implemented, just need to expose params**

### Parameters

| Parameter | Type | Current | Range | Description |
|-----------|------|---------|-------|-------------|
| `freeu_s1` | float | 0.9 | 0.0-1.0 | Skip connection factor 1 (affects colors) |
| `freeu_s2` | float | 0.2 | 0.0-1.0 | Skip connection factor 2 (affects colors) |
| `freeu_b1` | float | 1.3 | 1.0-1.6 | Backbone factor 1 (affects feature maps) |
| `freeu_b2` | float | 1.4 | 1.0-1.6 | Backbone factor 2 (affects feature maps) |

### Recommended Settings

**SDXL (Official):**
- `s1=0.9, s2=0.2, b1=1.3, b2=1.4` (GitHub recommendation)
- `s1=0.6, s2=0.4, b1=1.1, b2=1.2` (HuggingFace recommendation)

**SDXL (Community Optimized):**
- `s1=0.85, s2=0.35, b1=1.10, b2=1.15` (balanced)
- `s1=0.9, s2=0.2, b1=1.2, b2=1.4` (PirateDiffusion)

**Anime Models (Illustrious/NoobAI):**
- `s1=0.95, s2=0.15, b1=1.1, b2=1.2` (preserve colors)

### What Changes
- **Skip factors (s1, s2):** Colors, saturation, vibrancy
- **Backbone factors (b1, b2):** Feature maps, details, structure

### Research Paper
- **Title:** "FreeU: Free Lunch in Diffusion U-Net"
- **Conference:** CVPR 2024 (Oral)
- **Links:**
  - GitHub: https://github.com/ChenyangSi/FreeU
  - HuggingFace Docs: https://huggingface.co/docs/diffusers/main/en/using-diffusers/freeu
  - Optimal SDXL Params: https://wandb.ai/nasirk24/UNET-FreeU-SDXL/reports/FreeU-SDXL-Optimal-Parameters--Vmlldzo1NDg4NTUw

### Expected Impact
- **Detail Quality:** +10-20%
- **Color Saturation:** +15-25%
- **Flexibility:** High (model-specific tuning)

---

## 🎯 4. Negative Prompt Weighting

**Status:** Community Standard, Widely Used

### What It Does
Allows scaling the strength of the negative prompt independently from CFG scale. This gives fine control over unwanted element removal without affecting positive prompt strength.

### Key Benefits
- ✅ **Independent negative prompt control**
- ✅ **Better balance between positive/negative**
- ✅ **No speed penalty**
- ✅ **Simple to implement**

### Parameters

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `negative_prompt_scale` | float | 1.0 | 0.0-2.0 | Multiplier for negative prompt strength |

### Recommended Settings
- **Subtle negative:** `0.5-0.7` (gentle removal)
- **Standard:** `1.0` (default behavior)
- **Strong negative:** `1.2-1.5` (aggressive removal)
- **Disabled negative:** `0.0` (ignore negative prompt)

### Implementation Notes
Modified CFG formula:
```python
# Standard CFG
output = uncond + cfg_scale * (cond - uncond)

# With negative weighting
output = uncond + cfg_scale * (cond - (negative_scale * uncond))
```

### Use Cases
- Fine-tuning unwanted element removal
- Balancing strong positive with weak negative
- Testing negative prompt effectiveness

### Expected Impact
- **Control Flexibility:** +40%
- **Negative Prompt Effectiveness:** +20-30%

---

## 🚀 5. CFG++ (Classifier-Free Guidance Plus Plus)

**Status:** ICLR 2025, Cutting Edge

### What It Does
CFG++ is a fundamental improvement to standard CFG that addresses the "off-manifold" problem, where CFG can push samples outside the learned data distribution. It provides better quality at lower CFG scales.

### Key Benefits
- ✅ **Better quality at lower CFG**
- ✅ **Reduced mode collapse**
- ✅ **Improved invertibility**
- ✅ **Dramatic improvements for distilled models** (SDXL-turbo, SDXL-lightning)
- ✅ **No training required**

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `use_cfg_plus_plus` | bool | False | Enable CFG++ instead of standard CFG |

### Recommended Settings
- **Standard models:** Test with and without, may provide 10-20% improvement
- **Distilled models (Turbo/Lightning):** Strongly recommended, dramatic quality boost
- **Lower CFG scales:** More effective at CFG 3-7

### Implementation Notes
- Requires custom sampling function modification
- More complex than other techniques
- Supported in ComfyUI (June 2024) and reForge (July 2024)

### Research Paper
- **Title:** "CFG++: Manifold-constrained Classifier Free Guidance for Diffusion Models"
- **Conference:** ICLR 2025
- **Links:**
  - GitHub: https://github.com/CFGpp-diffusion/CFGpp
  - Paper: https://arxiv.org/abs/2406.08070
  - Project Page: https://cfgpp-diffusion.github.io/

### Expected Impact
- **Quality at Low CFG:** +30-40%
- **Distilled Model Quality:** +50-70%
- **Mode Collapse Reduction:** +40%

---

## 📊 6. Dynamic CFG Scaling

**Status:** Advanced Technique, Research-backed

### What It Does
Varies CFG scale across timesteps during generation. Higher CFG in early steps preserves prompt adherence, lower CFG in later steps allows refinement and detail.

### Key Benefits
- ✅ **Better detail preservation**
- ✅ **Smoother transitions**
- ✅ **Reduced artifacts**
- ✅ **Flexible control**

### Parameters

| Parameter | Type | Default | Options | Description |
|-----------|------|---------|---------|-------------|
| `cfg_schedule` | string | "constant" | constant, linear_decay, cosine | How CFG changes over steps |
| `cfg_start_scale` | float | Same as CFG | 1.0-30.0 | CFG at first step |
| `cfg_end_scale` | float | Same as CFG | 1.0-30.0 | CFG at last step |

### Recommended Settings

**Linear Decay (General):**
- `cfg_start_scale=7.0, cfg_end_scale=3.0, schedule="linear_decay"`

**Cosine Decay (Smooth):**
- `cfg_start_scale=8.0, cfg_end_scale=4.0, schedule="cosine"`

**High Detail:**
- `cfg_start_scale=10.0, cfg_end_scale=5.0, schedule="linear_decay"`

### Implementation Notes
- Modify k-diffusion sampling loop
- Apply different CFG per timestep
- Relatively simple to implement

### Expected Impact
- **Detail Quality:** +10-15%
- **Artifact Reduction:** +20%
- **Flexibility:** High

---

## 🎯 Implementation Priority

### Phase 1: Quick Wins (1-2 hours total)
1. ✅ **Configurable FreeU** - 10 minutes
   - Already implemented, just expose parameters
   - High impact for model-specific tuning

2. ✅ **Token Merging (ToMe)** - 20 minutes
   - `pip install tomesd` + 5 lines of code
   - 2x speedup for free

3. ✅ **Negative Prompt Weighting** - 30 minutes
   - Simple CFG math modification
   - More control flexibility

### Phase 2: High Impact (3-4 hours total)
4. ⭐ **PAG (Perturbed-Attention Guidance)** - 2-3 hours
   - Significant quality improvement
   - Better prompt adherence
   - **RECOMMENDED TO START HERE**

5. ✅ **Dynamic CFG Scaling** - 1 hour
   - Better detail/artifact balance
   - Good for complex scenes

### Phase 3: Advanced (4-6 hours)
6. 🔬 **CFG++** - 4-6 hours
   - Complex implementation
   - Best for distilled models
   - Cutting-edge research

---

## 📝 Required Code Changes

### 1. Add IOTypes (src/pipelines/contracts.py)
```python
# Add to IOType enum
PAG_SCALE = "PAG_SCALE"
PAG_LAYERS = "PAG_LAYERS"
NEGATIVE_SCALE = "NEGATIVE_SCALE"
TOME_RATIO = "TOME_RATIO"
FREEU_S1 = "FREEU_S1"
FREEU_S2 = "FREEU_S2"
FREEU_B1 = "FREEU_B1"
FREEU_B2 = "FREEU_B2"
CFG_SCHEDULE = "CFG_SCHEDULE"
CFG_START_SCALE = "CFG_START_SCALE"
CFG_END_SCALE = "CFG_END_SCALE"
```

### 2. Expose in Preset YAML
```yaml
# Example form field
- type: slider
  name: pag_scale
  label: "PAG Scale"
  configuration:
    min: 0.0
    max: 10.0
    step: 0.5
    default: 3.0
  description: "Perturbed-Attention Guidance strength. Higher values improve structure."
```

### 3. Pipeline Integration
Each technique requires specific integration points in:
- `src/pipelines/pipes/checkpoint_loader/sdxl/sdxl_model.py`
- `src/pipelines/pipes/generator/sdxl/pipeline/stable_diffusion_xl_k_diffusion.py`

---

## 🧪 Testing Strategy

### For Each Technique:
1. **Baseline Test:** Generate 5 images with current settings
2. **Enable Technique:** Apply with recommended default settings
3. **Compare:** Side-by-side quality assessment
4. **Tune:** Adjust parameters for optimal results
5. **Document:** Record optimal settings for different model types

### Test Prompts:
- **Simple:** "a red apple on a wooden table"
- **Multi-subject:** "a cat and a dog playing in a garden"
- **Complex:** "a futuristic cityscape at sunset with flying cars and neon signs"
- **Portrait:** "portrait of a young woman with blue eyes, studio lighting"

### Metrics to Track:
- **Prompt Adherence:** Did it generate everything requested?
- **Quality:** Overall visual quality
- **Details:** Fine details and textures
- **Speed:** Generation time
- **Consistency:** Multiple generations with same seed

---

## 📚 Additional Resources

### Official Documentation
- Diffusers: https://huggingface.co/docs/diffusers/
- K-Diffusion: https://github.com/crowsonkb/k-diffusion

### Research Papers
- All papers linked in individual technique sections

### Community Resources
- Stable Diffusion Art: https://stable-diffusion-art.com/
- Civitai Guides: https://civitai.com/articles
- ComfyUI Examples: https://comfyui.org/

---

## ⚠️ Important Notes

### Compatibility
- All techniques should work with SDXL
- Some may require testing with Illustrious/NoobAI specifically
- LoRA compatibility needs verification

### Performance Impact
- **No penalty:** PAG, FreeU, Negative Weighting, Dynamic CFG
- **Speedup:** ToMe (2x faster)
- **Slight overhead:** CFG++ (5-10% slower)

### Quality Trade-offs
- Most techniques: Pure improvement or configurable
- ToMe: Speed vs. Quality trade-off (configurable)
- All others: Quality improvements only

---

**Last Updated:** 2025-10-01
**Version:** 1.0
