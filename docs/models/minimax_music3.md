---
type: model
title: MiniMax-Music3
family_key: minimax_music3
modes: [song]
spec:
  arch: MiniMaxMusic3Model — flow-matching DiT + fused condition encoder, conditioned on autoregressive frame hiddens (not a text-encoder cross-attention)
  params: LLM ~7.70B + depth decoder ~0.65B + DiT 2.457B + DAV 0.054B
  latent: 128-channel flow-matching latent, ~3.4453125 latents per AR frame (25 fps), DAV vocoder hop 512 (44.1 kHz stereo)
  vae: minimax_music3_dav (decode-only DAC-style vocoder)
  te: fused Qwen3-8B global LLM + 4-layer RVQ depth decoder + embedded tokenizer, one file
  guidance: "two independent CFG scales -- 1.5 AR, 1.7 flow-matching DiT"
  shift: "n/a (rectified-flow Euler with an inverted time convention, not a shift-parameterized schedule)"
  engine: native
files:
  - role: dit
    dir: models/diffusion_models
    note: flow-matching DiT with the condition encoder fused in (fp16 quality set; int8_convrot exists but is untested here)
  - role: text_encoder
    dir: models/text_encoders
    note: fused Qwen3-8B LLM + RVQ depth decoder + embedded tokenizer, pruned or full-vocab layout
  - role: vae
    dir: models/vae
    note: the DAV vocoder (decode-only, fp32 always)
---

# MiniMax-Music3

MiniMax-Music3 generates a full song, vocals and instrumentation together, from a caption and lyrics. Generation is two strictly sequential stages sharing one seed: an autoregressive stage (`arch/minimax_music3/{lm,ar_loop}.py`) samples a semantic code and 7 residual codes per frame at 25 fps from a KV-cached global LLM plus a small depth decoder, producing `frame_hiddens`; a windowed flow-matching stage (`arch/minimax_music3/{model,flow}.py`) turns those into a 128-channel latent the DAV vocoder decodes to 44.1 kHz stereo. There is no text-encoder cross-attention anywhere in the flow stage — the AR core's own hidden states, not an encoded prompt, are the DiT's conditioning.

The two stages are never resident together on a 24GB card: the AR stage's LM unit is offloaded, its `MODELS` cache entry explicitly evicted, and this frame's own reference to it dropped BEFORE the DiT places for the flow stage (`generator/audio_minimax_music3`'s module docstring) — the same failure shape as the MiniMax-H3 mode-switch RAM OOM if that ordering is skipped.

## Files & detection

Three files, all required, no optional component:

- **The DiT** (`diffusion_models/minimax_music3_dit_fp16.safetensors`, 4.9GB, 374 tensors) is detection-keyed on `cond_layer_logits` + `latent_conditioners.0.weight` + the fused `transformer.layers.0.self_attn.to_qkv.weight` — the condition encoder that mixes the AR stage's 8 per-frame hidden slots is fused into the same file as the 36-block transformer, not a separate module.
- **The text encoder** (`text_encoders/minimax_music3_text_encoder_pruned_bf16.safetensors`, 16.7GB, 328 tensors) fuses the 36-layer Qwen3-8B global LLM, the 4-layer RVQ depth decoder, the residual-code embedding table, and the checkpoint's own tokenizer (an embedded `tokenizer_json` U8 tensor, captured before the tensor is stripped from the state dict — the Gemma4 idiom). Detection carries **five independent layout booleans** rather than assuming they move together: `embed_tokens_prefill`/`lm_head_pruned` (pruned vs. full vocab), the LLM's `qkv_proj`/`gate_up_proj` fusion, and the depth decoder's own merged-qkv/merged-mlp flags. Both the pruned and full-vocab headers are exercised by the detection tests.
- **The DAV vocoder** (`vae/minimax_music3_dav.safetensors`, 217MB, 121 tensors, all F32) is a decode-only DAC-style module — no encoder, so there is no way to condition on reference audio (see Limitations). Its repack keeps `weight_g`/`weight_v` two-parameter weight-norm spelling verbatim (unlike the MiniMax-H3 audio repack, which ships pre-folded plain weights); the vae loader folds `weight = weight_g * weight_v / ||weight_v||_(dims 1,2)` once at load time. The fold is dim-agnostic across `Conv1d` (dim 0 = out_channels) and `ConvTranspose1d` (dim 0 = in_channels under `torch.nn.utils.weight_norm`'s default `dim=0`) because reducing over dims (1, 2) and keeping dim 0 is correct for both — the two conv types never need separate fold logic.

## Presets & modes

`content/presets/marketplace/MiniMax-Music3` ships a single `song` mode. There
is no plain-field caption form — the Generation tab carries only `seed`; every
real submission arrives as a [Music Director](../music-director.md) document
(`frontend/src/lib/components/music-director/`), the ONLY caption/lyrics/
duration surface. The preset declares `t2m` and `director` composition modes
alongside `song` (no `references` — see Limitations); the editor has no
visible mode switch (mirrors Video Director's modeless Stage & Rail
precedent) — `mode` is derived from the document's structure
(`deriveMusicDirectorMode` in `frontend/src/lib/utils/musicDirector.ts`) plus
an explicit "Instrumental (no vocals)" toggle, which derives `t2m` (no lyrics
capability at all) rather than a separate `instrumental` preset mode; the
pipeline composes the literal `"[instrumental]"` lyrics string whenever the
submitted mode is `t2m`. `modes/song/pipeline.yml`'s `caption` is the
document's `description` and its `lyrics` is `compiled_lyrics` (the document's
`sections` timeline serialized to bracket-tagged lyrics, `compile_sections_to_lyrics`)
— both empty-safe for a doc-less API/MCP submission.

## Prompt contract

The assembled prompt is part of the checkpoint contract — even whitespace changes shift downstream token ids — so `arch/minimax_music3/prompt.py`'s `clean_caption`/`normalize_lyrics`/`build_prompt` are a byte-for-byte port of the diffusers reference, never a reimplementation from the model card's prose. Two things worth knowing when a caption or lyrics block behaves unexpectedly:

- `clean_caption` strips markdown (headings, bullets, bold/italic) but keeps the line, and flattens `<|a b|>`-style special tags to `"a is b"` — this is why training captions read `"bpm is 128"` rather than a labeled field, and why this preset's plain-text `"Label:\n..."` section headings pass through untouched.
- `normalize_lyrics` follows the **diffusers** variant, not ComfyUI's: text sharing a line with a leading bracket tag is **dropped**, tags are lowercased one per line, `" ^ "` becomes a line break, and `[start]` is prepended automatically (never type it). ComfyUI's own `normalize_lyrics` keeps the tag-line text instead — "sounds different from Comfy" is not a bug signal for this family.

Fixed special-token ids are asserted against the checkpoint's own embedded tokenizer blob at construction rather than trusted blindly (`IM_START=151644`, `AUDIO_CFG=151654`, `AUDIO_START=151669`, `AUDIO_END=151670`, `CAPTION_START=151671`/`CAPTION_END=151672`, `LYRICS_START=151673`/`LYRICS_END=151674`, `AUDIO_CODE_OFFSET=151675`); the unconditional CFG branch is the same ids with `[1:-2]` overwritten to `AUDIO_CFG`. Hard caps: `MAX_PROMPT_TOKENS=5000`, `MAX_AUDIO_FRAMES=9000` (360s) — past `prompt_tokens + frames + 1 > 10240` the LLM's RoPE silently extrapolates past its trained position range rather than erroring; the pipe surfaces this as a warning, not a hard stop, since it is a quality cliff on the tail of a long song, not a broken request.

## Sampling

Default generation parameters: 30 flow-matching Euler steps, seed drives both stages, 60s default duration (360s hard cap; MiniMax validated up to 300s — 300-360s is best-effort in this port, unmeasured on real weights).

Two independent CFG scales, unlike every image/video family this engine documents which carries at most one: `ar_cfg_scale` (default 1.5) guides the AR stage's per-frame semantic-and-residual-code sampling, restricted to the conditional branch's own top-50 before guiding (`top_k`, default 50; there is no temperature parameter anywhere in this family's sampler); `cfg_scale` (default 1.7) guides the flow-matching DiT, with the unconditional branch a **zeroed condition tensor** rather than a second AR pass. **The AR-CFG default is an open question, not a settled one**: both reference implementations (diffusers and ComfyUI) default to 1.5, but MiniMax's own published workflow ships 1.7 — this port ships 1.5 and exposes both as advanced knobs pending a real-weight GPU A/B.

A song longer than one ~200-AR-frame window (≈689 latents) denoises as a sequence of overlapping windows (200-frame window, 100-frame hop) that each start from the previous window's trailing latents, blended toward them at every Euler step rather than only at the seam (`arch/minimax_music3/flow.py`'s `denoise_windowed`) — deliberately **not** ComfyUI's overlap-add scheme, so cross-engine audio differing at window boundaries is expected, not a bug. The flow-matching time convention is inverted from every other family this engine documents: `t=0` is noise and `t=1` is data (re-derived as a standalone closed form from `FlowMatchEulerDiscreteScheduler(num_train_timesteps=1, shift=1.0, invert_sigmas=True)`, not imported). Decode-side, each window is vocoded independently and cropped in sample space (not latent space) before concatenation, so a window's own decode always has correct convolutional context at its edges.

## Limitations

- **Text-to-music only.** The shipped checkpoint has no audio encoder — the DAV vocoder is decode-only — so there is no style/reference-audio, `extend`, or `repaint` composition mode, and the preset's Music Director declaration carries no `references` key at all. `docs/music-director.md`'s own "Music3-like preset" example predates this finding and still shows `director.references: "whole"` — do not copy that example for this family; it needs correcting.
- **Per-window latent-length rounding is independent per window, not globally consistent.** `latent_length(num_frames) = floor(num_frames * 3.4453125)` is called once per denoising window with that window's own frame count (`arch/minimax_music3/model.py`), not derived as a slice of one latent sequence computed from the song's total frame count. Each window's own floor is internally exact, but nothing has verified that the accumulated per-window truncation matches a single global computation bit-for-bit on real audio — flagged as an open precision question pending GPU validation, not a known bug.
- **The AR core's hidden state is read after the final RMSNorm, an assumption rather than a directly-confirmed reference match.** `_GlobalLM.prefill`/`.step` (`arch/minimax_music3/lm.py`) return `self.model.norm(x)`, and that post-norm value is both what `lm_head` scores AND what gets concatenated into `frame_hidden`, the DiT's own conditioning input. Reading the LM head off a post-norm hidden state is the standard transformer convention this port followed for both consumers; whether the condition encoder specifically wants the pre-norm hidden state instead is unconfirmed against real weights.
- **`int8_convrot` is supported at the ops layer, unexercised for this family.** `vendor/gpl/comfyui/ops.py` already parses `comfy_quant` descriptors and builds the convrot dequant path (verified against the real int8 headers in `ai/minimax_music3/`: the TE quantizes exactly its 160 block linears, the DiT its 144, everything else stays bf16/fp16), but wiring `quantization_metadata` through this family's module builders and GPU-validating quality is unimplemented — phase 1 loads bf16/fp16 only.
- **No LoRA support in this preset.** SimpleTuner Music3 LoRAs exist in the wild; out of v1 scope.
- **Determinism holds same-seed-same-device only.** Sampled discrete AR tokens flip under any numeric perturbation (dtype, offload, device) — there is no cross-device/dtype golden audio, by the reference implementation's own policy, not a gap in this port.
- **GPU validation is pending.** No end-to-end run on real weights has been performed for this family — everything above describes the implemented contract, not a measured result. `tests/manual/native_smoke.py`-style validation and the AR-CFG 1.5-vs-1.7 A/B are open runbook items.

## License

MiniMax-Music3's weights are published under the MiniMax Music3 Community License. Unlike MiniMax-H3's license, its Applicable Territory carries **no exclusions** — there is no region where the license withholds use of the weights or their outputs, which is why this preset ships in `content/presets/marketplace/` rather than needing a private/local-only path. Two obligations still apply regardless of territory: a commercial product's interface must display "MiniMax-Music3" prominently, and a hosted deployment serving the model to others takes on a safeguards duty (content-safety and misuse-prevention measures, per the license text). The license also requires separate written authorization above US$20M annual revenue, the same trigger MiniMax-H3's license uses. PotionUI ships this preset; it does not ship or download the weights, and the license is between the deployer and MiniMax.

## Hardware

**No local weight files exist and no GPU run has ever been performed for this family.** The quality set (pruned bf16 TE + fp16 DiT + fp32 DAV) is 23.6GB on disk and an estimated ~23GB VRAM peak during the AR stage, fitting a 24GB card only via the stage-handoff eviction discipline above — there is no supportable tier recommendation until real-weight validation exists. The `int8_convrot` mixed set (int8 TE + fp16 DiT, 14.3GB disk) is the officially shipped default and the intended path below 24GB, but is unvalidated in this port (see Limitations).
