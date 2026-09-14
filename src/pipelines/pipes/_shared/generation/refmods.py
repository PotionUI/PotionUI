"""RefMod bundles: pre-encoded MiniMax-H3 reference latents shipped as a
single `.safetensors` file, ported from the community `ComfyUI-MiniMaxH3Mod`
project (github.com/Luisacaotica/ComfyUI-MiniMaxH3Mod, MIT license).

A RefMod is a lightweight alternative to a `ref2va` image/video/audio
reference: instead of a full-resolution source file the checkpoint's own VAE
re-encodes on every request, a RefMod ships an already-encoded, deliberately
TINY latent (a raw VAE-encode output, average-pooled down -- e.g. a visual
member's spatial extent might be 4x4) baked once by the authoring tool. That
tiny latent enters the SAME `ref2va` reference pipeline as any other
reference -- own-resolution prefix, not an overlay -- so a 4x4 visual member
contributes exactly one small reference block, the same way a normal image
reference keeps its own resolution rather than the target canvas
(`conditioning.py`'s module docstring).

**File format** (upstream `core.py`/`bundle.py`): the safetensors header's
`metadata()` carries ONE key, `refmod_meta` (`audio_refmod_meta` for
audio-only files written by older versions), whose value is a JSON object;
files from before the header move carry the same object in a `<file>.json`
sidecar. A bundle has `kind: "bundle"`, `name` and `members`
(`[{"kind", "name", ...}, ...]`), member `i`'s latent under tensor `ref_i`.
A single-member file has `kind`/`name` at the top level and its latent under
tensor `latent`.

**Tensor shapes**, in the VAE's own RAW (NOT mean/std-normalized) latent
space -- the exact space `MiniMaxH3VideoVAE`/`MiniMaxH3AudioVAE.encode()`
return before `conditioning.normalize_visual_latent`/`audio.
_normalize_and_pack_audio_latent` apply the checkpoint's per-channel
normalization:

- `"image"`/`"video"`: `[1, 24, t, h, w]`, the same `(B, C, F, H, W)`
  convention `condition_latents` already carries in `conditioning.py` --
  handed straight through, no reshape.
- `"audio"`: `[1, 32, 2, T]` (batch, latent_channels, audio_channels, time) --
  reshaped here to the `(audio_channels, latent_channels, T)` convention
  `audio.py`'s `pack_audio_rows`/`_normalize_and_pack_audio_latent` consume.

**Strength.** A RefMod's own `strength` (`w`, in `[0, 1]`) blends its latent
toward a BLURRED copy of itself: `w * z + (1 - w) * blur(z)`, where `blur`
spatially (visual) or temporally (audio) adaptive-avg-pools the latent down
by half and upsamples it back with nearest-neighbor -- a coarsening that
stays on the latent manifold rather than a pixel-space effect. `w <= 0`
drops the member outright (it contributes nothing); `w >= 1` is the
identity (returned verbatim, not `1*z + 0*blur(z)`, to keep it bit-exact).

These local channel-count constants mirror `generator/video_minimax_h3/
main.py`'s own `VIDEO_LATENT_CHANNELS`/`AUDIO_LATENT_CHANNELS` and `audio.py`'s
`AUDIO_CHANNELS` rather than importing them: this module is imported BY
`generator/video_minimax_h3/main.py` and `prompt_encoder/main.py` alike, and
importing back from the former would cycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import torch
import torch.nn.functional as F
from safetensors import safe_open

Tensor = torch.Tensor

VIDEO_LATENT_CHANNELS = 24
AUDIO_LATENT_CHANNELS = 32
AUDIO_CHANNELS = 2


@dataclass(frozen=True)
class RefMod:
    """One RefMod bundle member, already strength-mixed and shape-normalized
    into the convention `conditioning.ReferenceMedia.latent`/`audio.
    _normalize_and_pack_audio_latent` expect -- still in the VAE's RAW (un-
    normalized) latent space; mean/std normalization happens downstream,
    once, alongside every other reference (module docstring)."""

    kind: str
    name: str
    latent: Tensor
    strength: float
    source: str


def apply_strength(latent: Tensor, w: float) -> Tensor:
    """`w * z + (1 - w) * blur(z)` -- see module docstring "Strength".

    `latent` is `(B, C, T, H, W)` (visual) or `(channels, latent_channels,
    T)` (audio); `blur` pools whichever axes are spatial for that shape (H/W
    for visual, T for audio) down to half their size, floored at 1, then
    upsamples back with nearest-neighbor. `w >= 1.0` short-circuits to `latent`
    itself -- exact identity, not a `1*z + 0*blur` that a NaN/inf in `blur`
    could still poison.
    """
    w = float(w)
    if w >= 1.0:
        return latent
    if latent.dim() == 5:
        b, c, t, h, width = latent.shape
        flat = latent.permute(0, 2, 1, 3, 4).reshape(b * t, c, h, width)
        pooled = F.adaptive_avg_pool2d(flat, (max(1, h // 2), max(1, width // 2)))
        blurred = F.interpolate(pooled, size=(h, width), mode="nearest")
        blurred = blurred.reshape(b, t, c, h, width).permute(0, 2, 1, 3, 4)
    elif latent.dim() == 3:
        channels, latent_channels, num_frames = latent.shape
        pooled = F.adaptive_avg_pool1d(latent, max(1, num_frames // 2))
        blurred = F.interpolate(pooled, size=num_frames, mode="nearest")
    else:
        raise ValueError(f"refmods.apply_strength: unsupported latent shape {tuple(latent.shape)}")
    return w * latent + (1.0 - w) * blurred


def _reshape_audio_latent(raw: Tensor, *, source: str, member_index: int) -> Tensor:
    if raw.dim() != 4 or raw.shape[0] != 1 or raw.shape[2] != AUDIO_CHANNELS:
        raise ValueError(
            f"RefMod {source!r} member {member_index} (audio) must be a "
            f"[1, latent_channels, {AUDIO_CHANNELS}, T] latent, got {tuple(raw.shape)}"
        )
    return raw.squeeze(0).permute(1, 0, 2).contiguous()


def _validate_visual_latent(raw: Tensor, *, kind: str, source: str, member_index: int) -> Tensor:
    if raw.dim() != 5 or raw.shape[0] != 1 or raw.shape[1] != VIDEO_LATENT_CHANNELS:
        raise ValueError(
            f"RefMod {source!r} member {member_index} ({kind}) must be a "
            f"[1, {VIDEO_LATENT_CHANNELS}, t, h, w] latent, got {tuple(raw.shape)}"
        )
    return raw


META_KEYS = ("refmod_meta", "audio_refmod_meta")


def _read_meta(metadata: Mapping[str, str], file_path: str) -> dict[str, Any]:
    """The upstream metadata block: one JSON string in the safetensors header
    under `refmod_meta` (audio-only files: `audio_refmod_meta`), or, for files
    written by older versions, a `<file>.json` sidecar next to the tensors."""
    for key in META_KEYS:
        if key in metadata:
            try:
                meta = json.loads(metadata[key])
            except (TypeError, ValueError) as error:
                raise ValueError(f"RefMod {file_path!r}: header {key!r} is not valid JSON") from error
            break
    else:
        sidecar = Path(file_path).with_suffix(".json")
        if not sidecar.is_file():
            raise ValueError(
                f"RefMod {file_path!r}: no {META_KEYS[0]!r} header and no {sidecar.name} sidecar -- not a RefMod file"
            )
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        raise ValueError(f"RefMod {file_path!r}: metadata block must be a JSON object")
    return meta


def _parse_members(meta: Mapping[str, Any], *, source: str) -> list[dict[str, Any]]:
    """`[{"kind", "name", "tensor_key"}, ...]`: a bundle (`kind == "bundle"`,
    tensors `ref_{i}` per `members` entry) or a single-member file (tensor
    `latent`, kind/name at the top level)."""
    if meta.get("kind") == "bundle":
        members = meta.get("members")
        if not isinstance(members, list) or not members:
            raise ValueError(f"RefMod bundle {source!r}: 'members' must be a non-empty list")
        parsed = []
        for index, member in enumerate(members):
            if not isinstance(member, dict) or "kind" not in member:
                raise ValueError(f"RefMod bundle {source!r}: member {index} is missing its 'kind'")
            parsed.append({"kind": member["kind"], "name": member.get("name"), "tensor_key": f"ref_{index}"})
        return parsed
    kind = meta.get("kind")
    if kind is None:
        raise ValueError(f"RefMod {source!r}: metadata has no 'kind'")
    return [{"kind": kind, "name": meta.get("name"), "tensor_key": "latent"}]


def _load_bundle(file_path: str, strength: float) -> list[RefMod]:
    with safe_open(file_path, framework="pt") as handle:
        keys = set(handle.keys())
        members = _parse_members(_read_meta(handle.metadata() or {}, file_path), source=file_path)

        mods: list[RefMod] = []
        for index, member in enumerate(members):
            tensor_key = member["tensor_key"]
            if tensor_key not in keys and len(keys) == 1 and len(members) == 1:
                tensor_key = next(iter(keys))
            if tensor_key not in keys:
                raise ValueError(
                    f"RefMod bundle {file_path!r}: missing tensor {tensor_key!r} for member {index}"
                )
            raw = handle.get_tensor(tensor_key).float()
            kind = member["kind"]
            if kind in ("image", "video"):
                latent = _validate_visual_latent(raw, kind=kind, source=file_path, member_index=index)
            elif kind == "audio":
                latent = _reshape_audio_latent(raw, source=file_path, member_index=index)
            else:
                raise ValueError(
                    f"RefMod bundle {file_path!r}: member {index} has kind {kind!r}, expected "
                    f"'image', 'video' or 'audio'"
                )
            latent = apply_strength(latent, strength)
            name = member.get("name") or f"{file_path}#{index}"
            mods.append(RefMod(kind=kind, name=name, latent=latent, strength=strength, source=file_path))
        return mods


def load_refmods(entries: Sequence[Mapping[str, Any]] | None) -> list[RefMod]:
    """`reference_mods` config (a list of `{"file_path", "strength"}` dicts)
    -> every member of every referenced bundle, strength-mixed and shape-
    normalized, in FILE order then MEMBER order.

    An entry whose `strength` is `<= 0` is skipped entirely -- module
    docstring "Strength" -- so its members never reach `pack_references`.
    A bad file (unreadable, malformed metadata, wrong tensor shape) raises
    immediately with the offending file/member named, rather than failing
    deep inside VAE encode with a bare shape-mismatch.
    """
    mods: list[RefMod] = []
    for entry in entries or []:
        file_path = entry["file_path"]
        strength = float(entry.get("strength", 1.0))
        if strength <= 0:
            continue
        mods.extend(_load_bundle(file_path, strength))
    return mods
