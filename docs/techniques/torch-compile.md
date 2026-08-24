---
type: technique
title: Regional torch.compile
category_group: Performance
status: needs-gpu-validation
families: [all-native]
authors: []
paper: null
reference_impl: null
knobs:
  - key: NATIVE_TORCH_COMPILE
    surface: env
    default: "off"
    effect: "Compiles the repeated transformer block of a fully GPU-resident DiT for faster steps after warm-up"
related: [first-block-cache, fp8-matmul]
---

# Regional torch.compile

Compiling an entire diffusion transformer with PyTorch's `torch.compile` normally costs a
multi-minute warm-up the first time it runs, which is a poor fit for an interactive app. PotionUI
instead compiles only the *repeated transformer block* — the same block class that gets stacked N
times to build the network — and reuses one compiled artifact for every repetition. Warm-up becomes
the cost of compiling a single block's graph, not the whole model, while the surrounding control
flow (guidance branching, first-block-cache skip decisions) stays in ordinary eager Python and
never breaks the compiled graph.

Once compiled, later denoising steps that hit the same block shapes run faster because PyTorch's
Inductor backend has fused and optimized the block's operations. The compiled state is reversible:
PotionUI automatically restores the original, uncompiled blocks whenever the model leaves the GPU
(offload, unload, or a move back to CPU), so a model cached in host RAM is always the plain,
uncompiled version.

## When to use it

Best for repeated generations against the same model where the compile warm-up cost on the first
run is amortized over many subsequent steps or requests — long sessions on one preset/model rather
than one-off generations or frequent model switching. It only ever engages when the model is fully
GPU-resident; it has no effect during low-VRAM streamed (partial-residency) generation.

## How to enable it

Set the environment variable before starting the API server:

```bash
export NATIVE_TORCH_COMPILE=on
```

`auto` currently behaves identically to `on` (there is no separate heuristic yet). Any other value
is treated as `off` with a warning logged. There is no admin panel toggle or preset key for this —
the environment variable is the only surface.

## Tradeoffs and limitations

- First-generation cost: the initial forward through a newly compiled block pays the Inductor
  compile time before it becomes faster on subsequent calls.
- Requires full GPU residency: skipped automatically (falls back to eager, no error) if the model is
  running under partial/streamed residency, carries active runtime LoRA deltas (cast-mode), or uses
  quantized (`Fp8ScaledLinear`/`Nvfp4Linear`) linears — none of those are compile-compatible in the
  current implementation.
- Any compile failure at first-forward time degrades gracefully to eager execution (logged, not
  fatal) rather than aborting the generation.
- Applies only to the `native` engine family DiTs; SDXL runs on a separate diffusers-based stack and
  is unaffected.
- Reaches every native family, not just the resident-sample path `NativeGenerator` drives directly
  (Flux, Qwen-Image, Z-Image, Krea-2, Anima, …). The video / bring-your-own-loop families — LTX
  (`generator/txt2vid_ltx`, `generator/video_ltx`, `detailer/video_ltx`), DFR
  (`generator/dfr_video_ltx`), and MiniMax-H3 (`generator/video_minimax_h3`) — drive their own
  sampling loop and place the DiT through `place_dit_for_sequence` (`src/pipelines/pipes/_shared/
  generation/dit_placement.py`), which calls the same gate on every resident placement. Wan 2.1/2.2
  (`generator/txt2vid_wan22`, `generator/img2vid_wan22`, `generator/chain_video_wan22`) place their
  high/low-noise experts through `_ExpertRouter._place_expert`, which offers each expert to the same
  gate independently the moment it lands fully resident. SeedVR2 (`generator/seedvr2`) subclasses
  `NativeGenerator` and calls the inherited `_maybe_compile()` right after its own `_move_dit_to_gpu`,
  same as the image path.
- Steady-state speedup and output numerical drift versus eager have not been benchmarked on real
  hardware — validate wall-clock and image diffs before relying on it for production throughput.
