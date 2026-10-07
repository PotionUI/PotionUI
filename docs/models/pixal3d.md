---
title: Pixal3D
category: Presets / Models
---

# Pixal3D

Pixal3D (TencentARC, MIT) is TRELLIS.2 with one changed block and a new conditioner. The VAEs, the sampler, the DiT trunks, the latent layouts, the normalisation constants and the per-stage sampler settings are TRELLIS.2's. Two things change:

- Every flow block's cross-attention gets a second path. The block computes `cross_attn(x, global) + proj_linear(proj)`, where `global` is the 5 global DINOv3 tokens (CLS and 4 registers) instead of TRELLIS.2's full token sequence, and `proj` is the image feature at the point where that voxel lands in the image.
- A camera model projects each voxel into the image and bilinearly samples the DINOv3 patch grid there. The shape and texture stages also sample a NAF-upsampled feature map and concatenate it, so their per-voxel feature is 2048 wide (1024 for the sparse-structure stage).

The port lives in `src/platform/runtime/native/arch/pixal3d/` and reuses `arch/trellis2/` for everything that did not change: the flow models (with a projection-attention variant chosen by config), the VAEs, the sampler, the decoders and the mesh export.

## Files & detection

Weights come from `Comfy-Org/Pixal3D`, a bf16 repack of TencentARC's fp32 checkpoints in the same four-prefix layout as the TRELLIS.2 bundle (`model.structure_model.`, `model.img2shape_512.`, `model.img2shape.`, `model.shape2txt.`). Each Pixal3D block adds `cross_attn.proj_linear.{weight,bias}` and moves the attention weights under `cross_attn.cross_attn_block.`. A `blocks.0.cross_attn.proj_linear.weight` probe tells a Pixal3D bundle from a TRELLIS.2 one, and each loader refuses the other's bundle.

| file | size | notes |
|---|---|---|
| `diffusion_models/pixal3d_bf16.safetensors` | 11.0 GB | single-view bundle, used by `img2mesh` |
| `diffusion_models/pixal3d_multiview_bf16.safetensors` | 11.0 GB | multi-view bundle, used by `views2mesh` |
| `clip_vision/dino_v3_L_naf_fp32.safetensors` | 1.2 GB | DINOv3 ViT-L/16 plus 37 `naf.image_encoder.*` keys |
| `vae/trellis_2_shape_vae_bf16.safetensors` | 1.1 GB | byte-identical to the TRELLIS.2 file |
| `vae/trellis_2_texture_vae_bf16.safetensors` | 0.95 GB | byte-identical to the TRELLIS.2 file |

The single-view and multi-view bundles have byte-identical key spaces and shapes. Only the file name tells them apart: a name containing `multiview` (also `multi_view`, `multi-view` or an `mv` token) is the multi-view bundle. Each mode refuses the other bundle. The NAF weights are read from the `naf.` prefix of the DINOv3 file. The plain TRELLIS.2 `dino_v3_vit_l` file has no NAF weights and is refused.

The `pixal3d-starter` recipe fetches the single-view set. The VAEs are the same files `trellis2-starter` fetches, so an instance with TRELLIS.2 reuses them. The multi-view bundle is listed in the recipe as optional but is not downloaded. The `views2mesh` model picker offers it as a recommendation.

Licences: Pixal3D code and weights are MIT (TencentARC). The NAF upsampler weights are Apache-2.0 (valeoai/NAF). The DINOv3 weights are under Meta's DINOv3 Licence.

## Cascade

The cascade is TRELLIS.2's, with these differences:

- **Tiers.** Only 1024 and 1536. There is no 512 tier, because Pixal3D ships no texture flow for it. 1536 is upstream's default and the heaviest on VRAM.
- **HR requantisation.** Coordinates are requantised as `round((c + 0.5) / 512 · (R - 1))` instead of TRELLIS.2's `floor((c + 0.5) / 512 · R)`. The projection grid is `linspace(-1, 1, R)` (index / (R - 1), not cell centres), and the two have to agree.
- **Crop.** The subject's alpha bounding box is padded by 1.1 before resizing (TRELLIS.2 uses 1.0), composited on black.
- **Projection per token.** Upstream builds the dense `R³` feature grid and gathers the active rows, which is 7.25 GB per stage at the 1536 tier. The math is pointwise, so this port projects only the active voxels.

Per stage, the image is encoded at 512 px (sparse structure, shape-LR) or 1024 px (shape-HR, texture). NAF upsamples to 512² for the shape stages and 1024² for texture. The sparse-structure stage has no NAF features.

## Camera

The camera is a fixed front view with a pinhole model, and its distance follows from the field of view: `d = 0.5 / tan(fov / 2)`, so the centre slice of the unit cube spans the image width. The FOV is set by hand (Camera FOV, default 49.13°, upstream's default). A wrong FOV misplaces every voxel's sample. Upstream suggests a much narrower FOV for inputs that come out distorted, and telephoto photos, product shots and AI-generated renders usually want about 12–20°. Upstream estimates the FOV from the photo with MoGe-2. That auto-FOV path is deferred and not ported.

The mesh comes out posed as the camera saw it, not turned to a canonical front, because the training latents were encoded in each view's camera frame.

## Multi-view

`views2mesh` takes the rig the multi-view checkpoint was trained with: a 4-view orbit at 0/90/180/270° azimuth (front, left, back, right), elevation 0, FOV 20°, distance `1.1 · 0.5 / tan(fov / 2)`. The slots match ComfyUI's `Pixal3DMultiViewConditioning` node. Front is required, and the other views are optional. Views are matted but never cropped or rescaled, because their framing has to match the cameras.

Each view is projected separately through `F · inv(C0) · Ci`, which re-bases view `i` onto the canonical front camera `F`. The projected features are averaged across views, and so are the global tokens. The fused conditioning therefore has the single-view shape, and the DiTs are architecturally identical to the single-view ones. They are separately fine-tuned weights, though.

## NAF

NAF is a 2.7 MB guided feature upsampler (valeoai/NAF, Apache-2.0). It runs once per stage on the DINOv3 patch grid. Its only attention op is natten's dilated 9×9 neighbourhood attention. K and V are upsampled from the low-res grid by exactly the dilation factor, so that op is exactly "attend to the 9×9 low-res cells around your own cell", with the window shifted to stay inside the grid at the borders, never padded. The port implements that form in pure torch (`arch/pixal3d/naf.py`). natten is not a dependency, and the high-res K/V tensors natten's call site builds are never materialised. ComfyUI's pure-torch version zero-pads at the borders instead, so expect border-band differences against ComfyUI outputs.

## Export orientation

Upstream and ComfyUI export the mesh 180° apart about the up axis. Upstream's composite transform is `(x, y, z) → (-x, y, -z)`. ComfyUI exports the mesh as decoded (identity). The preset's Export Orientation select defaults to upstream's frame (`upstream`); `camera` gives ComfyUI's. Which one faces the default glTF camera is pending GPU validation.

## Presets & modes

The shipped preset is `content/presets/marketplace/Pixal3D/`, with two modes:

- `img2mesh` (Image to 3D): one source image, Camera FOV (default 49.13°), Detail Level 1024 / 1536 (default 1536).
- `views2mesh` (Views to 3D): named Front (required), Left, Back and Right image slots, each loaded by its own media loader, and Camera FOV (default 20°).

Both use `model_loader/pixal3d` and `generator/pixal3d`, thin subclasses of the TRELLIS.2 pipes. The loader's `bundle_mode` (`single` / `multiview`) is fixed per mode in `pipeline.yml`.

## Mesh export

Pixal3D decodes through the TRELLIS.2 path unchanged: FlexiDualGrid shape decoder, `dual_grid`, then `postprocess` (remesh, decimation, UV unwrap, projection onto the pre-decimation surface, bake, GLB). The preset carries TRELLIS.2's Decimation Target (default 300k faces, up to 1M) and Unwrap Quality (Fast, Balanced, Best). Only the export frame is new: it is applied to the decoded volume before that chain, as a proper rotation of both the vertices and the attribute voxels, so the remesh, the winding and the texture lookup are unaffected. The 1536 default makes the export heavier than TRELLIS.2's 1024 default: denser raw meshes, so the remesh and the decimation take longer at the same target. A profiled Pixal3D run writes the same `[TRELLIS2_MESH]` stage log and the same dumps as TRELLIS.2: `raw_mesh.npz`, `texture_volume.npz` (in the export frame), `cond_image.png` (the front view as conditioned) and `tex_slat.npz`.

## Hardware

`requires:` asks for 16 GB minimum and recommends 24 GB. The flows run one at a time, so the flow peak is one bf16 flow (~2.8 GB of weights) plus the stage's tokens. The conditioning adds a transient peak of about 4–5 GB at the 1024² NAF stages, with no flow resident. CPU RAM holds the four flows (~11 GB), as with TRELLIS.2. These are estimates from the spike, not measurements.
