---
type: model
title: YuE2
family_key: yue2
modes: [song]
spec:
  arch: YuE2Model — a causal LM whose DecoderLayer carries a SECOND, NAR-only set of attention/MLP projections trained for non-autoregressive flow-matching over VAE latents; the LM IS the conditioning (no cross-attention text encoder)
  params: ~3B (AR/NAR backbone, single checkpoint)
  latent: 64-channel flow-matching latent (`_EXPECTED_LATENT_DIM`), Oobleck VAE decoder, hop 1920 → 48 kHz
  vae: yue2_vae (decode-only Oobleck decoder)
  te: none — the same AR/NAR backbone both samples codec tokens and is its own conditioning
  guidance: "one CFG scale for the codec-token sampling stage -- 1.01 default when cot='off', 1.0 otherwise"
  shift: "n/a (const-prediction NAR synthesis, not a shift-parameterized diffusion schedule)"
  engine: native
files:
  - role: dit
    dir: models/diffusion_models
    note: AR/NAR backbone -- `vae2llm`/`llm2vae` are the two Linear bridges between the LM's hidden state and the VAE's latent space
  - role: vae
    dir: models/vae
    note: Oobleck decoder (decode-only, fp32)
---

# YuE2

YuE2 generates a full song, vocals and instrumentation together, from a short style-tag list and lyrics. Generation is two stages sharing one seed: an autoregressive stage (`arch/yue2/{lm,ar_loop}.py` -- referenced through `arch/yue2/ar_loop.py`) samples a codec token per frame at 25 fps, optionally preceded by a chain-of-thought pass that writes an ABC transcription the codec stage conditions on; a non-autoregressive flow-matching stage (`arch/yue2/nar.py`) then turns the sampled codec tokens into a 64-channel latent an Oobleck VAE (`arch/yue2/vae.py`) decodes to a waveform. There is no cross-attention text encoder anywhere in this family — the same backbone that samples codec tokens is its own conditioning, the same shape as MiniMax-Music3's fused TE-is-the-AR-core design but with no separate fused-TE file: one checkpoint carries both roles.

The AR/NAR backbone is offloaded and its `MODELS` cache entry explicitly evicted (`generator/audio_yue2`'s `_release_lm`) before the VAE places for the decode stage — the same failure shape as the MiniMax-H3 mode-switch RAM OOM if that ordering is skipped.

## Files & detection

Two files, both required, no optional component:

- **The AR/NAR backbone** (`diffusion_models/yue2_3b_bf16.safetensors`) is checkpoint-native — `YuE2ForCausalLM` in upstream's `modeling_yue2.py` — keyed on `vae2llm.weight` + `model.layers.0.nar_self_attn.q_proj.weight` (`unet_detect.py:_detect_yue2`, `arch/yue2/detect.py:detect_yue2_role`). A separate Comfy-repack layout is also recognized (`vae2llm.weight` + `model.layers.0.self_attn.qkv_proj.weight`), keyed independently since the two spellings fuse the LM's QKV projection differently. `latent_dim` is read off `vae2llm.weight`'s second dimension and asserted to equal 64 at load time (`model_loader/yue2`'s `_EXPECTED_LATENT_DIM` check) — a checkpoint with a different latent width fails loudly rather than silently mis-decoding.
- **The Oobleck VAE** (`vae/yue2_vae_fp32.safetensors`) is decode-only — `YuE2VAE` in upstream's `modeling_vae.py`; an `encoder.*` prefix, if also present in the checkpoint, is not read. Keyed on `decoder.layers.0.weight_v` + `decoder.layers.8.weight_v` (`vae_detect.py:detect_yue2_vae_config`), reporting `latent_dim` (from the first conv's input channels) and `out_channels` (from the last conv's output channels). Sample rate 48 kHz, downsampling ratio 1920.

Filename-based detection also exists as a fallback (`detect_yue2_role_from_filename`): a name containing `yue2-vae`/`yue2_vae`/`yue2vae` resolves to the VAE role, and `yue2-3b`/`yue2_3b`/`yue2` (checked in that order, after the VAE markers) resolves to the LM role — the two recommended filenames above (`yue2_3b_bf16.safetensors`, `yue2_vae_fp32.safetensors`) match unambiguously either way.

## Presets & modes

`content/presets/marketplace/YuE2` ships a single `song` mode. There is no structured caption form — the Generation tab's "Style tags" field is free text (genre/instruments/vocal character/language/tempo), and lyrics are the standard [Prompt section](../presets.md) (the same segmented editor every image preset uses), not a Director document. `modes/song/pipeline.yml`'s `style` is `form.style` and its `lyrics` is the resolved positive prompt — both empty-safe. There is no separate instrumental toggle: leaving the Prompt box empty already produces an instrumental track, since `lyrics` is an optional pipe config (`generator/audio_yue2`'s `validate_config` only requires `style`).

The Generation tab's "Chain of thought" select (`off`/`melody`/`full`) picks between direct codec sampling and the two ABC-transcription modes; the Advanced tab's ABC field lets a user supply a hand-written transcription instead of letting the model write its own, and is form-guarded (`pipeline.yml`) to render empty whenever `cot` is `off` — the generator pipe's own `validate_config` raises if `abc` is non-empty without a cot stage to condition, and the field's UI visibility toggle alone does not clear a stale value when the user switches `cot` back to `off`.

## Prompt contract

`arch/yue2/protocol.py:build_prompt_ids` assembles the AR prefix as `EOD, instruction, [Tags]\n<style>\n[Lyrics]\n<lyrics>\n, ABC_START[/abc ids/ABC_END], MUSIC_START` — a byte-for-byte port of the upstream reference, not a reimplementation from the model card's prose. Lyrics follow the same Suno-style bracket-tag convention this engine's other music family (MiniMax-Music3) uses: `[Verse]`/`[Chorus]`/`[Bridge]`/`[Outro]` and similar, one tag per line, with any text sharing a tag's line dropped rather than sung.

Fixed special-token ids: `EOD=151643`, `ABC_START/END=151847/151848`, `MUSIC_START/END=151851/151852`, `CODEC_OFFSET=151853` (`CODEC_SIZE=32768`), `LATENT_START/END/PAD=184621/184622/184623`, `VOCAB_SIZE=184704`, `CONTEXT=24576`. `INSTRUCTIONS` carries one instruction string per `cot` value (`off`/`melody`/`full`), each explicit about whether the codec stage samples directly or conditions on a written ABC transcription.

## Sampling

Two sequential passes per song, both driven by the same seed:

- **Codec-token sampling** (`ar_loop.generate`, phase `"semantic"`) — `Sampling` defaults: `temperature=1.0`, `top_p=0.95`, `top_k=100`, `repetition_penalty=1.2`, `penalty_window=50`, `min_tokens=200`, `max_tokens=9000` (clamped to the requested duration's frame budget, 25 fps, 360s hard cap). CFG scale defaults to 1.01 when `cot="off"` and 1.0 otherwise (`protocol.guidance_scale`), overridable in `[0, 20]`; the unconditional branch is only built when `cfg_scale != 1.0`.
- **ABC transcription** (when `cot != "off"` and no hand-written ABC is supplied) — its own `Sampling`: `temperature=0.7`, `top_p=0.9`, `top_k=30`, `repetition_penalty=1.005`, `penalty_window=100`, `min_tokens=32`, `max_tokens=4096`. Must reach its own end token within budget or the generation fails outright (`generator/audio_yue2` raises rather than silently truncating the transcription).
- **NAR synthesis** (`nar.synthesize`) — flow-matching midpoint-Euler, `nar_steps` default 32, over the codec tokens produced above, seeded the same as the AR stage.

`duration=0` (auto) generates until the AR stage's own stop token, up to the 360s hard cap (`MAX_SEMANTIC_TOKENS = 360 * 25`); with `auto_duration` set, hitting the token budget without stopping is treated as a failed generation, not a silent cutoff — the same policy MiniMax-Music3 uses for its own duration cap.

## Editing the score

With Chain of thought on Melody or Full, the model writes an ABC score before it samples any audio. That score is shown as soon as it exists: `generator/audio_yue2` emits it as a `text` pipe artifact (`TextGenerationOutput`, serialized as `artifact_type: "text"`) titled `ABC transcription · melody` or `ABC transcription · full`. It appears under the Workbench in the live workspace and in the generation's Artifacts section in history, rendered in mono with a copy button. When you supplied your own ABC, the pipe emits that text instead, titled `ABC (yours) · <mode>`, so every run records the score it actually performed.

To edit the model's score and play it again:

1. Generate with Chain of thought on Melody or Full and a fixed seed.
2. On the ABC artifact, press **Use as ABC**. It writes the text into the Advanced tab's ABC field and sets Chain of thought to the mode that produced it. The action is generic: a text artifact names a form field and extra field values, and the Workbench writes them into the active tab's form.
3. Edit the score in the ABC field (mono, 12 rows), for example change a phrase, transpose a bar or swap a chord symbol under Full, then generate again with the same seed.

The model skips its own transcription stage and conditions the codec stage on your edited score. It still samples audio from that score, so a same-seed rerun follows your edits but is not a note-for-note render.

The ABC field is checked before the model runs. A non-empty score needs an `X:` (reference number) line and a `K:` (key) header line. The field shows this inline as you type, and the server rejects a submission without them with a field error (a `pattern` on the `string` field, enforced by the form binder). The check is skipped while Chain of thought is Off, because the field is hidden and ignored then.

## Limitations

- **Text-to-music only.** No audio encoder ships, so there is no style/reference-audio, extend, or repaint mode — the same limitation MiniMax-Music3 documents for the same reason.
- **No LoRA support in this preset.** Out of v1 scope.
- **Determinism holds same-seed-same-device only.** Sampled discrete AR tokens flip under any numeric perturbation (dtype, offload, device) — no cross-device/dtype golden audio, the same policy every AR-sampled family in this engine follows.
- **GPU validation is pending; detection signatures are unverified against real weight headers.** The detection keys above were derived from the upstream architecture's known tensor names, not confirmed against a downloaded checkpoint's actual state-dict keys — treat them as synthetic until a real header dump validates them. No end-to-end run on real weights has been performed for this family.

## License

YuE2-3B's weights (both the AR/NAR backbone and the Oobleck VAE) are published under the CC BY-NC 4.0 license (Creative Commons Attribution-NonCommercial 4.0 International): reuse and adaptation are permitted with attribution, but commercial use is not. PotionUI ships this preset; it does not ship or download the weights, and the license is between the deployer and the model's authors — see `m-a-p/YuE2-3B` and `m-a-p/YuE2-Vae` on Hugging Face for the license text.

## Hardware

**No local weight files exist and no GPU run has ever been performed for this family.** On-disk size is roughly 7.8 GB (7.26 GB backbone + 530 MB VAE); there is no supportable VRAM tier recommendation until real-weight validation exists.
