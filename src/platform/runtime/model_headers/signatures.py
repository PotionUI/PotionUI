from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any

FLUX_SIG = "double_blocks.0.img_attn.norm.key_norm.scale"
FLUX2_SIG = "double_stream_modulation_img.lin.weight"
KREA2_SIG = "txtfusion.projector.weight"
QWEN_IMAGE_SIG = "transformer_blocks.0.attn.add_q_proj.weight"
QWEN_IMAGE21_SIG = "modulation.1.weight"
QWEN_IMAGE21_SIG2 = "txt_in.text_norm.weight"
WAN_SIG = "head.modulation"
LTX_SIG = "adaln_single.emb.timestep_embedder.linear_1.weight"
ANIMA_SIG = "llm_adapter.blocks.0.cross_attn.q_proj.weight"
LUMINA2_SIG = "cap_embedder.1.weight"
SEEDVR2_SIG = "vid_in.proj.weight"
SEEDVR2_SIG2 = "blocks.0.ada.vid.attn_shift"
MINIMAX_H3_SIG = "video_patch_proj.weight"
MINIMAX_H3_SIG2 = "audio_patch_proj.weight"
MINIMAX_MUSIC3_SIG = "cond_layer_logits"
MINIMAX_MUSIC3_SIG2 = "latent_conditioners.0.weight"
MINIMAX_MUSIC3_SIG3 = "diffusion_transformer.transformer.layers.0.self_attn.to_qkv.weight"
YUE2_SIG = "vae2llm.weight"
YUE2_SIG2 = "model.layers.0.nar_self_attn.q_proj.weight"
TRELLIS2_STRUCTURE_PREFIX = "model.structure_model."
TRELLIS2_SHAPE_PREFIX = "model.img2shape."
TRELLIS2_SHAPE_512_PREFIX = "model.img2shape_512."
TRELLIS2_TEXTURE_PREFIX = "model.shape2txt."
WAN_REJECT: dict[str, str] = {
    "vace_patch_embedding.weight": "vace",
    "control_adapter.conv.weight": "camera / control-adapter",
    "casual_audio_encoder.encoder.final_linear.weight": "s2v (audio)",
    "audio_proj.audio_proj_glob_1.layer.bias": "humo (audio)",
    "face_adapter.fuser_blocks.0.k_norm.weight": "animate",
}

SD_UNET_SIG = "input_blocks.0.0.weight"
SD_LABEL_EMB_SIG = "label_emb.0.0.weight"
SD_CONTEXT_PROBES = (
    "input_blocks.4.1.transformer_blocks.0.attn2.to_k.weight",
    "input_blocks.1.1.transformer_blocks.0.attn2.to_k.weight",
    "middle_block.1.transformer_blocks.0.attn2.to_k.weight",
)
SD3_PREFIX = "joint_blocks.0."
CHROMA_PREFIX = "distilled_guidance_layer."

DENOISER_PREFIXES = ("model.diffusion_model.", "diffusion_model.")
EXCLUSIVE_DENOISER_PREFIX = "net."

TRANSFORMER_EXTRACTABLE_FAMILIES = frozenset(
    {
        "flux",
        "krea2",
        "qwen_image",
        "qwen_image21",
        "z_image",
        "wan",
        "anima",
        "seedvr2",
        "minimax_h3",
    }
)
BNB_QUANT_SUFFIXES = (".absmax", ".quant_map")
BNB_QUANT_STATE_MARKER = ".quant_state.bitsandbytes__"

ShapeOf = Callable[[str], "tuple[int, ...] | None"]


@dataclass(frozen=True)
class FamilyMatch:
    family: str
    variant: str | None = None
    model_type: str | None = None
    transformer_extractable: bool = False


def detect_denoiser_prefix(keys: Collection[str]) -> str | None:
    if keys and all(k.startswith(EXCLUSIVE_DENOISER_PREFIX) for k in keys):
        return EXCLUSIVE_DENOISER_PREFIX
    return detect_prefix(keys, DENOISER_PREFIXES)


def has_bnb_quantization(keys: Collection[str]) -> bool:
    return any(k.endswith(BNB_QUANT_SUFFIXES) or BNB_QUANT_STATE_MARKER in k for k in keys)


def is_transformer_extractable(match: FamilyMatch, keys: Collection[str], file_format: str) -> bool:
    if file_format != "safetensors" or match.family not in TRANSFORMER_EXTRACTABLE_FAMILIES:
        return False
    if match.family == "wan" and match.variant in WAN_REJECT.values():
        return False
    return not has_bnb_quantization(keys)


def strip_prefix(sd: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    return {k[len(prefix):]: v for k, v in sd.items() if k.startswith(prefix)}


def detect_prefix(sd: Collection[str], candidates: Collection[str]) -> str | None:
    best: str | None = None
    best_count = 0
    for prefix in candidates:
        count = sum(1 for k in sd if k.startswith(prefix))
        if count > best_count:
            best = prefix
            best_count = count
    return best


def _dim(shape_of: ShapeOf, key: str, axis: int) -> int | None:
    shape = shape_of(key)
    if shape is None or len(shape) <= axis:
        return None
    return int(shape[axis])


def _has_prefix(keys: Collection[str], prefix: str) -> bool:
    return any(k.startswith(prefix) for k in keys)


def _wan_variant(keys: Collection[str], shape_of: ShapeOf) -> str:
    for key, label in WAN_REJECT.items():
        if key in keys:
            return label
    in_dim = _dim(shape_of, "patch_embedding.weight", 1)
    if in_dim == 48:
        return "ti2v"
    if in_dim == 36 or "img_emb.proj.0.bias" in keys:
        return "i2v"
    return "t2v"


def _seedvr2_variant(keys: Collection[str]) -> str:
    return "3b" if "blocks.0.mlp.vid.proj_in_gate.weight" in keys else "7b"


def family_from_keys(keys: Collection[str], shape_of: ShapeOf) -> FamilyMatch | None:
    trellis_prefixes = (
        TRELLIS2_STRUCTURE_PREFIX,
        TRELLIS2_SHAPE_PREFIX,
        TRELLIS2_SHAPE_512_PREFIX,
        TRELLIS2_TEXTURE_PREFIX,
    )
    if all(_has_prefix(keys, p) for p in trellis_prefixes):
        return FamilyMatch("trellis2")

    if KREA2_SIG in keys:
        return FamilyMatch("krea2")

    if QWEN_IMAGE21_SIG in keys and QWEN_IMAGE21_SIG2 in keys:
        return FamilyMatch("qwen_image21")

    if QWEN_IMAGE_SIG in keys and "txt_norm.weight" in keys:
        return FamilyMatch("qwen_image")

    if WAN_SIG in keys:
        return FamilyMatch("wan", _wan_variant(keys, shape_of))

    if LTX_SIG in keys and "patchify_proj.weight" in keys:
        variant = "ltxav" if "audio_adaln_single.linear.weight" in keys else "ltxv"
        return FamilyMatch("ltx", variant)

    if LUMINA2_SIG in keys:
        return FamilyMatch("z_image" if _dim(shape_of, LUMINA2_SIG, 0) == 3840 else "lumina2")

    if ANIMA_SIG in keys:
        return FamilyMatch("anima")

    if SEEDVR2_SIG in keys and SEEDVR2_SIG2 in keys:
        return FamilyMatch("seedvr2", _seedvr2_variant(keys))

    if MINIMAX_H3_SIG in keys and MINIMAX_H3_SIG2 in keys:
        return FamilyMatch("minimax_h3")

    if MINIMAX_MUSIC3_SIG in keys and MINIMAX_MUSIC3_SIG2 in keys and MINIMAX_MUSIC3_SIG3 in keys:
        return FamilyMatch("minimax_music3")

    if YUE2_SIG in keys and YUE2_SIG2 in keys:
        return FamilyMatch("yue2")

    if FLUX_SIG not in keys or "img_in.weight" not in keys:
        return None
    return FamilyMatch("flux", "flux2" if FLUX2_SIG in keys else "flux1")


def sd_family_from_keys(keys: Collection[str], shape_of: ShapeOf) -> FamilyMatch | None:
    if SD_UNET_SIG in keys:
        context = None
        for probe in SD_CONTEXT_PROBES:
            context = _dim(shape_of, probe, 1)
            if context is not None:
                break
        inpaint = _dim(shape_of, SD_UNET_SIG, 1) == 9
        if SD_LABEL_EMB_SIG in keys:
            if context == 2048:
                return FamilyMatch("sdxl", "inpaint" if inpaint else "base")
            if context == 1280:
                return FamilyMatch("sdxl", "refiner")
            return None
        if context == 768:
            return FamilyMatch("sd1", "inpaint" if inpaint else "base")
        if context == 1024:
            return FamilyMatch("sd2", "inpaint" if inpaint else "base")
        return None

    if _has_prefix(keys, SD3_PREFIX):
        return FamilyMatch("sd3")
    if FLUX_SIG in keys and _has_prefix(keys, CHROMA_PREFIX):
        return FamilyMatch("chroma")
    return None


GGUF_ARCHITECTURE_FAMILIES = {
    "flux": "flux",
    "sdxl": "sdxl",
    "sd1": "sd1",
    "sd2": "sd2",
    "sd3": "sd3",
    "wan": "wan",
    "ltxv": "ltx",
    "lumina2": "lumina2",
    "qwen_image": "qwen_image",
    "hidream": "hidream",
    "hyvid": "hunyuan_video",
    "cosmos": "cosmos",
}


def family_from_gguf_architecture(architecture: str | None) -> FamilyMatch | None:
    if not architecture:
        return None
    family = GGUF_ARCHITECTURE_FAMILIES.get(architecture.strip().lower())
    return FamilyMatch(family) if family else None
