# SDXL base pipeline config (vendored, offline)

Diffusers-format `model_index.json` + per-component `config.json` / tokenizer
files for the SDXL base pipeline, vendored so `StableDiffusionXLKDiffusionPipeline
.from_single_file()` never needs to reach the Hugging Face Hub on first load.

## Why this exists

`from_single_file()` only converts *weights* from the checkpoint; it still needs
a diffusers-format config (architecture hyperparameters + tokenizer vocab) to
know how to shape each component (`unet`, `vae`, `text_encoder`,
`text_encoder_2`, `tokenizer`, `tokenizer_2`, `scheduler`) before loading the
checkpoint's tensors into it. When `config=` is not given explicitly, diffusers
infers `stabilityai/stable-diffusion-xl-base-1.0` from the checkpoint's key
names and fetches this same config bundle via `snapshot_download`. The native
engine sets `HF_HUB_OFFLINE=1` by default
(`src/platform/runtime/native/text_encoders/tokenization.py`), so that fetch
raises `LocalEntryNotFoundError` on any machine without a warm HF cache —
breaking SDXL on every fresh install. `src/pipelines/pipes/checkpoint_loader/
sdxl/sdxl_model.py` now passes `config=<this directory>` explicitly, which
diffusers resolves as a local path — see `single_file.py`'s
`from_single_file`: when `config` is a directory,
`cached_model_config_path = config` and every sub-component load resolves via
`<config>/<component>/...` with no hub call, regardless of `local_files_only`.

## Provenance

- **Source repo:** `stabilityai/stable-diffusion-xl-base-1.0`
  (https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0)
- **Commit:** `462165984030d82259a11f4367a4eed129e94a7b`
- **License:** CreativeML Open RAIL++-M (`license:openrail++` on the source
  repo). These are architecture/tokenizer config files, not model weights —
  no `.safetensors`/`.bin`/`.pt`/`.ckpt` files are included or ever will be;
  weights are supplied by the user's own checkpoint file at generation time.
- **Fetched with:** `huggingface_hub.snapshot_download(allow_patterns=
  ["**/*.json", "*.json", "**/*.txt", "*.txt", "**/*.model"])` — the exact
  same allow-list diffusers' own
  `single_file._download_diffusers_model_config_from_hub` uses, so this
  bundle is byte-identical to what `from_single_file()` would otherwise
  download on first (online) use.
- **Files (13, ~3.2 MB total, no weights):**
  - `model_index.json` — `_class_name` changed from `StableDiffusionXLPipeline`
    to `StableDiffusionXLKDiffusionPipeline` (this repo's actual pipeline
    class) so diffusers doesn't cross-filter config keys against the upstream
    class (`ConfigMixin.extract_init_dict`); every other key is unmodified.
  - `scheduler/scheduler_config.json`
  - `text_encoder/config.json` (CLIP-L, `CLIPTextModel`)
  - `text_encoder_2/config.json` (OpenCLIP ViT-bigG, `CLIPTextModelWithProjection`)
  - `tokenizer/{vocab.json,merges.txt,tokenizer_config.json,special_tokens_map.json}`
  - `tokenizer_2/{vocab.json,merges.txt,tokenizer_config.json,special_tokens_map.json}`
  - `unet/config.json`
  - `vae/config.json`

## Note on tokenizer duplication

`tokenizer/` and `tokenizer_2/` carry byte-identical `vocab.json`/`merges.txt`
to the CLIP-L tokenizer already bundled at
`src/platform/runtime/native/text_encoders/assets/clip_l_tokenizer/` (verified
via `diff`) — SDXL's `tokenizer_2` uses the same standard 49408-token CLIP BPE
vocab as `tokenizer`, just a different `pad_token` (`"!"` vs
`"<|endoftext|>"`). They are duplicated here rather than shared because: (1)
`from_single_file`'s `config=` contract requires each component to live at a
real `<config>/<subfolder>/` path — no support for pointing a single component
at an arbitrary external directory — and (2) `pipelines/` must not gain a
structural dependency on a specific `platform/runtime/native` asset layout for
a loader path that is otherwise self-contained. The `tokenizer`/`tokenizer_2`
objects diffusers builds here are never actually used for encoding — SDXL
prompt encoding goes through `SDXLParameterAdapter` /
`ConditioningModel` with precomputed embeddings (see
`StableDiffusionXLKDiffusionPipeline.encode_prompt`'s docstring) — so their
only job is to construct without error.

## Updating

If PotionUI ever needs to track a newer SDXL base revision, re-run the fetch
that produced this bundle and re-apply the `_class_name` edit above:

```python
from huggingface_hub import snapshot_download
path = snapshot_download(
    "stabilityai/stable-diffusion-xl-base-1.0",
    allow_patterns=["**/*.json", "*.json", "**/*.txt", "*.txt", "**/*.model"],
)
```

Then copy `model_index.json`, `scheduler/`, `text_encoder/`, `text_encoder_2/`,
`tokenizer/`, `tokenizer_2/`, `unet/`, `vae/` into this directory (skip
`vae_1_0/`, `vae_decoder/`, `vae_encoder/` — onnx/refiner-adjacent folders this
pipeline doesn't use) and verify no weight files snuck in.
