---
title: RIFE
category: Presets / Models
---

# RIFE

RIFE (Practical-RIFE, hzwer, MIT) makes the frames between two video frames from their optical flow. It runs in the `interpolate` mode of the **Video Tools** preset, through the `interpolator/rife` pipe and the IFNet vendored in `vendor/rife/` (provenance in `vendor/NOTICE.md`). There is no text encoder, sampler or seed: the same clip and checkpoint give the same frames.

## Checkpoints

Checkpoints go in `models/vfi/`. The loader reads the variant from the tensor names and shapes of the file:

| Line | Network | Tested against |
|---|---|---|
| rife46 to rife49 | four flow blocks on the scales 8/4/2/1, a two-layer feature encoder from rife47 on, frames padded to a multiple of 128 px | the published key layouts of rife-4.6, rife47 and rife49 |
| 4.21 to 4.26 | a multi-scale `Head` feature encoder, flow blocks that hand a feature map to the next one, frames padded to a multiple of 64 px; four blocks on 8/4/2/1, or five on 16/8/4/2/1 (4.25, 4.26) | the published key layouts of 4.22, 4.22.lite, 4.25 and 4.26 |

The 4.21+ files also carry 40 tensors that only training uses (`teacher.*`, `caltime.*`). The loader drops those two prefixes and loads everything else strictly, so any other unknown or missing tensor is refused. It refuses by name 4.10 to 4.20 and 4.24, whose blocks pass no features, and 4.25.lite, whose scale ladder (32/16/8/4/1) cannot be read from its weights.

RIFE 4.25 and 4.26 are published as `flownet_v4.25.pkl` and `flownet_v4.26.pkl`, an extension the model indexer does not read. The Video Tools recommendations save them as `flownet_v4.25.pth` and `flownet_v4.26.pth`, which `torch.load` reads the same way. RIFE 4.25 is the first recommendation, then 4.26 and rife49.

## Frame rate

**Multiply** (`target_fps` 0, `factor` 2 or 4): `N` source frames give `N × factor` frames at the source rate times `factor`. Between each pair the frames at `1/factor` to `(factor − 1)/factor` are made, and the last source frame is held for the slots it owns, which keeps the duration. Multiply never looks for cuts: every pair is blended.

**Target frame rate** (`target_fps` above 0, up to 240; Video Tools offers 24, 25, 30, 48, 50 and 60): `N` source frames give `round(N × target / source)` frames, at least one. Output frame `k` sits at source position `k × source / target`:

- on a source frame, that frame is written as it is;
- between frames `i` and `i + 1`, RIFE makes it at that exact instant;
- past the last source frame, the last frame is held.

Both rates are read as exact fractions, so a 23.976 fps source counts as 24000/1001. A position less than 1/1000 of a frame past a source frame snaps to it. Some examples:

| Source → target | Source frames written as they are | Instants made between two frames |
|---|---|---|
| 16 → 24 | one in two | 1/3, 2/3 |
| 24 → 60 | one in two | 0.2, 0.4, 0.6, 0.8 |
| 25 → 60 | one in five | eleven instants |
| 25 → 24 | the first of every 25 | 1/24, 2/24 and so on up to 23/24 |

The frames are written as they are made, and the clip never gets more frames than its final count.

**Cuts.** With a target frame rate and `scene_detection` on (the default, **Detect Cuts** in Video Tools), a pair whose grey 32 × 32 copies have a mean SSIM under 0.2 is taken as a cut. Its in-between slots show the nearer of the two frames, the earlier one below t = 0.5, and RIFE does not run on that pair. Turn it off for clips whose fast motion is read as cuts.

Both modes keep the duration, and `keep_audio` muxes the source audio back unchanged.
