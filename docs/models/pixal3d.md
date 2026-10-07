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
| `geometry_estimation/moge_2_vitl_normal_fp16.safetensors` (`Comfy-Org/MoGe`) | 0.66 GB | MoGe-2 camera estimator for Auto FOV; loaded only while Camera FOV is Auto |

The single-view and multi-view bundles have byte-identical key spaces and shapes. Only the file name tells them apart: a name containing `multiview` (also `multi_view`, `multi-view` or an `mv` token) is the multi-view bundle. Each mode refuses the other bundle. The NAF weights are read from the `naf.` prefix of the DINOv3 file. The plain TRELLIS.2 `dino_v3_vit_l` file has no NAF weights and is refused.

The `pixal3d-starter` recipe fetches the single-view set and the MoGe-2 file. The VAEs are the same files `trellis2-starter` fetches, so an instance with TRELLIS.2 reuses them. The multi-view bundle is listed in the recipe as optional but is not downloaded. The `views2mesh` model picker offers it as a recommendation.

Licences: Pixal3D code and weights are MIT (TencentARC). The NAF upsampler weights are Apache-2.0 (valeoai/NAF). The DINOv3 weights are under Meta's DINOv3 Licence. MoGe-2's code and weights are MIT (Microsoft, `Ruicheng/moge-2-vitl-normal`), on a DINOv2 ViT-L/14 backbone that Meta releases under Apache-2.0.

## Cascade

The cascade is TRELLIS.2's, with these differences:

- **Tiers.** Only 1024 and 1536. There is no 512 tier, because Pixal3D ships no texture flow for it. 1536 is upstream's default and the heaviest on VRAM.
- **HR requantisation.** Coordinates are requantised as `round((c + 0.5) / 512 · (R - 1))` instead of TRELLIS.2's `floor((c + 0.5) / 512 · R)`. The projection grid is `linspace(-1, 1, R)` (index / (R - 1), not cell centres), and the two have to agree.
- **Crop.** The subject's alpha bounding box is padded by 1.1 before resizing (TRELLIS.2 uses 1.0), composited on black.
- **Projection per token.** Upstream builds the dense `R³` feature grid and gathers the active rows, which is 7.25 GB per stage at the 1536 tier. The math is pointwise, so this port projects only the active voxels.

Per stage, the image is encoded at 512 px (sparse structure, shape-LR) or 1024 px (shape-HR, texture). NAF upsamples to 512² for the shape stages and 1024² for texture. The sparse-structure stage has no NAF features.

## Camera

The camera is a fixed front view with a pinhole model, and its distance follows from the field of view: `d = 0.5 / tan(fov / 2)`, so the centre slice of the unit cube spans the image width. A wrong FOV misplaces every voxel's sample.

Camera FOV has two settings. **Auto (MoGe-2)**, the `img2mesh` default, estimates the horizontal FOV from the image, as upstream's `inference.py` and ComfyUI's Pixal3D template both do by default. **Manual** takes the Field of View slider (49.13°, upstream's default when no estimate is made). Upstream suggests a much narrower FOV for inputs that come out distorted; telephoto photos, product shots and AI-generated renders usually want about 12–20°. Auto falls back to the slider value when MoGe-2 recovers no usable focal length, and says so.

### Auto FOV (MoGe-2)

The estimator is a pure-torch port of MoGe-2 in `src/platform/runtime/native/arch/moge/`, loaded by `model_loader/pixal3d` as its optional `camera_estimator` component (its own `MODELS` key, `native/moge/<file>`, fp32, about 1.3 GB resident). It is acquired only when the preset passes a file, and the preset passes one only while Camera FOV is Auto.

- **Input.** The image the conditioner sees: the matted, 1.1-padded square crop when background removal is on, the upload as given when it is off (front view in `views2mesh`), resized to 1024² and fed as RGB in [0, 1]. ComfyUI's template feeds MoGe its 1024² `ImageCropToMask` output, the same image.
- **Model.** DINOv2 ViT-L/14 (24 blocks, layers 5/11/17/23 projected and summed), a UV-conditioned conv neck, and the points and mask heads. The normal and metric-scale heads are skipped at load, because FOV needs neither. Resolution level 9 = 3600 tokens (a 60×60 grid on a square image). Compute is fp32 from the fp16 file, as in ComfyUI's MoGe-2 path.
- **Intrinsics.** The predicted affine point map (`xy·e^z, e^z`) and its mask (> 0.5) go through MoGe's own recovery: nearest-downsample to 64×64, then Levenberg-Marquardt over the z-shift with the focal solved in closed form (`scipy.optimize.least_squares`, `ftol=1e-3`, as upstream). The focal is relative to the half diagonal. With aspect `a`, `fx = f·√(1+a²)/(2a)` (normalised by width), and Pixal3D takes `fov_x = 2·atan(0.5 / fx)`, the axis upstream uses (`2·atan(W / 2fx_px)`) and ComfyUI wires (`MoGeGeometryToFOV`, `axis="horizontal"`).
- **Report.** The value used reaches the run as a "Camera FOV" text artifact on the generator pipe (`38.42° horizontal, estimated by MoGe-2`, or the fallback note) and an INFO log line.
- **Checkpoint.** Comfy-Org ships the `moge-2-vitl-normal` weights, which add a normal head to `moge-2-vitl`. Upstream Pixal3D loads `Ruicheng/moge-2-vitl` (a pickle `model.pt`, which the native engine does not load). Expect small FOV differences against upstream, and none against ComfyUI beyond float noise.

`views2mesh` keeps Manual at 20° as its default: the four cameras share one FOV, the shipped rig and most multi-view generators render at 20°, and ComfyUI's multi-view template runs no MoGe (its node's FOV widget defaults to 20). Auto is offered there for photographed views, estimated from the front view, as that node's tooltip suggests ("MoGeGeometryToFOV on one of the views for photos").

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

- `img2mesh` (Image to 3D): one source image, Camera FOV (default Auto (MoGe-2); Manual uses Field of View, default 49.13°), Detail Level 1024 / 1536 (default 1536).
- `views2mesh` (Views to 3D): named Front (required), Left, Back and Right image slots, each loaded by its own media loader, and Camera FOV (default Manual at 20°).

The Camera Estimator (MoGe-2) model field sits in the Models section of both modes. It is shown and required only while Camera FOV is Auto, so a manual run never needs the file.

Both use `model_loader/pixal3d` and `generator/pixal3d`, thin subclasses of the TRELLIS.2 pipes. The loader's `bundle_mode` (`single` / `multiview`) is fixed per mode in `pipeline.yml`.

## Mesh export

Pixal3D decodes through the TRELLIS.2 path unchanged: FlexiDualGrid shape decoder, `dual_grid`, then `postprocess` (remesh, decimation, UV unwrap, projection onto the pre-decimation surface, bake, GLB). The preset carries TRELLIS.2's Decimation Target (default 300k faces, up to 1M) and Unwrap Quality (Fast, Balanced, Best). Only the export frame is new: it is applied to the decoded volume before that chain, as a proper rotation of both the vertices and the attribute voxels, so the remesh, the winding and the texture lookup are unaffected. The 1536 default makes the export heavier than TRELLIS.2's 1024 default: denser raw meshes, so the remesh and the decimation take longer at the same target. A profiled Pixal3D run writes the same `[TRELLIS2_MESH]` stage log and the same dumps as TRELLIS.2: `raw_mesh.npz`, `texture_volume.npz` (in the export frame), `cond_image.png` (the front view as conditioned) and `tex_slat.npz`.

## Hardware

`requires:` asks for 16 GB minimum and recommends 24 GB. The flows run one at a time, so the flow peak is one bf16 flow (~2.8 GB of weights) plus the stage's tokens. The conditioning adds a transient peak of about 4–5 GB at the 1024² NAF stages, with no flow resident. CPU RAM holds the four flows (~11 GB), as with TRELLIS.2. These are estimates from the spike, not measurements.
