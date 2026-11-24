import contextlib
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence

import torch

from src.platform.runtime.guarded_import import guarded_from

IMAGE_PROJ_PREFIX = "image_proj."
IP_ADAPTER_PREFIX = "ip_adapter."
VISION_PREFIX = "vision_model."
HEADS_BY_WIDTH = {1280: 16, 1664: 16, 1024: 16, 768: 12}
VIT_H_PROJECTION = 1024
BIG_G_PROJECTION = 1280
VIT_H_HIDDEN = 1280


@dataclass(frozen=True)
class IPAdapterInfo:
    variant: str
    encoder: str
    uses_hidden_states: bool


def split_ip_adapter_state(flat: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    state: Dict[str, Dict[str, Any]] = {"image_proj": {}, "ip_adapter": {}}
    for key, value in flat.items():
        if key.startswith(IMAGE_PROJ_PREFIX):
            state["image_proj"][key[len(IMAGE_PROJ_PREFIX):]] = value
        elif key.startswith(IP_ADAPTER_PREFIX):
            state["ip_adapter"][key[len(IP_ADAPTER_PREFIX):]] = value
    if not state["image_proj"] or not state["ip_adapter"]:
        raise ValueError("it is not an IP-Adapter file")
    return state


def read_ip_adapter_state(file_path: str) -> Dict[str, Dict[str, Any]]:
    from safetensors.torch import load_file

    return split_ip_adapter_state(load_file(file_path))


def classify_ip_adapter(state: Mapping[str, Mapping[str, Any]]) -> IPAdapterInfo:
    projection = state["image_proj"]
    if "latents" in projection:
        width = int(projection["proj_in.weight"].shape[1])
        if width != VIT_H_HIDDEN:
            raise ValueError(f"The IP-Adapter Plus expects a {width}-wide image encoder, not CLIP ViT-H")
        return IPAdapterInfo(variant="plus", encoder="vit_h", uses_hidden_states=True)
    clip_dim = int(projection["proj.weight"].shape[1])
    if clip_dim == VIT_H_PROJECTION:
        return IPAdapterInfo(variant="plain", encoder="vit_h", uses_hidden_states=False)
    if clip_dim == BIG_G_PROJECTION:
        return IPAdapterInfo(variant="plain", encoder="big_g", uses_hidden_states=False)
    raise ValueError(f"The IP-Adapter reads {clip_dim}-wide image embeds, which no known image encoder produces")


ENCODER_NAMES = {"vit_h": "CLIP ViT-H", "big_g": "CLIP ViT-bigG"}
ENCODER_WIDTHS = {"vit_h": (VIT_H_HIDDEN, VIT_H_PROJECTION), "big_g": (1664, BIG_G_PROJECTION)}


def encoder_matches(encoder: Any, info: IPAdapterInfo) -> bool:
    hidden, projection = ENCODER_WIDTHS[info.encoder]
    config = encoder.config
    return int(config.hidden_size) == hidden and int(config.projection_dim) == projection


def clip_vision_config_kwargs(shapes: Mapping[str, Sequence[int]]) -> Dict[str, Any]:
    hidden = int(shapes[VISION_PREFIX + "embeddings.class_embedding"][0])
    patch = int(shapes[VISION_PREFIX + "embeddings.patch_embedding.weight"][-1])
    positions = int(shapes[VISION_PREFIX + "embeddings.position_embedding.weight"][0])
    side = math.isqrt(positions - 1)
    layer_ids = {
        key.split(".")[3]
        for key in shapes
        if key.startswith(VISION_PREFIX + "encoder.layers.")
    }
    return {
        "hidden_size": hidden,
        "intermediate_size": int(shapes[VISION_PREFIX + "encoder.layers.0.mlp.fc1.weight"][0]),
        "projection_dim": int(shapes["visual_projection.weight"][0]),
        "num_hidden_layers": len(layer_ids),
        "num_attention_heads": HEADS_BY_WIDTH.get(hidden, max(1, hidden // 64)),
        "image_size": side * patch,
        "patch_size": patch,
        "hidden_act": "gelu",
        "layer_norm_eps": 1e-5,
    }


def load_image_encoder(file_path: str, dtype: torch.dtype, device: Any):
    from safetensors.torch import load_file
    CLIPVisionConfig = guarded_from("transformers", "CLIPVisionConfig")
    CLIPVisionModelWithProjection = guarded_from("transformers", "CLIPVisionModelWithProjection")

    state = load_file(file_path)
    config = CLIPVisionConfig(**clip_vision_config_kwargs({key: value.shape for key, value in state.items()}))
    with torch.device("meta"):
        model = CLIPVisionModelWithProjection(config)
    result = model.load_state_dict({key: value.to(dtype) for key, value in state.items()}, strict=False, assign=True)
    if result.missing_keys:
        raise ValueError(f"The image encoder is missing weights such as {result.missing_keys[0]}")
    embeddings = model.vision_model.embeddings
    embeddings.register_buffer("position_ids", torch.arange(embeddings.num_positions).expand((1, -1)), persistent=False)
    return model.to(device=device, dtype=dtype).eval()


def encode_reference(image: Any, encoder: Any, info: IPAdapterInfo, device: Any, dtype: torch.dtype) -> torch.Tensor:
    CLIPImageProcessor = guarded_from("transformers", "CLIPImageProcessor")

    pixels = CLIPImageProcessor()(images=image.convert("RGB"), return_tensors="pt").pixel_values.to(device, dtype)
    with torch.no_grad():
        if info.uses_hidden_states:
            positive = encoder(pixels, output_hidden_states=True).hidden_states[-2]
            negative = encoder(torch.zeros_like(pixels), output_hidden_states=True).hidden_states[-2]
        else:
            positive = encoder(pixels).image_embeds
            negative = torch.zeros_like(positive)
    return torch.stack([negative, positive], dim=0)


@dataclass
class IPAdapterHandle:
    processors: Dict[str, Any]
    hid_proj: Any
    hid_type: Optional[str]


def ip_processor_types() -> tuple:
    module = "diffusers.models.attention_processor"
    return (guarded_from(module, "IPAdapterAttnProcessor"), guarded_from(module, "IPAdapterAttnProcessor2_0"))


def set_ip_adapter_scale(unet: Any, scale: float) -> None:
    kinds = ip_processor_types()
    for processor in unet.attn_processors.values():
        if isinstance(processor, kinds):
            processor.scale = [float(scale)]


def inject_ip_adapter(unet: Any, state: Mapping[str, Mapping[str, Any]], scale: float) -> IPAdapterHandle:
    handle = IPAdapterHandle(
        processors=dict(unet.attn_processors),
        hid_proj=unet.encoder_hid_proj,
        hid_type=unet.config.encoder_hid_dim_type,
    )
    unet._load_ip_adapter_weights([dict(state)])
    set_ip_adapter_scale(unet, scale)
    return handle


def remove_ip_adapter(unet: Any, handle: IPAdapterHandle) -> None:
    unet.set_attn_processor(handle.processors)
    unet.encoder_hid_proj = handle.hid_proj
    unet.config.encoder_hid_dim_type = handle.hid_type


@contextlib.contextmanager
def reference_session(
    unet: Any,
    adapter_path: str,
    encoder_path: str,
    image: Any,
    scale: float,
    device: Any,
    dtype: torch.dtype,
) -> Iterator[List[torch.Tensor]]:
    state = read_ip_adapter_state(adapter_path)
    info = classify_ip_adapter(state)
    encoder = load_image_encoder(encoder_path, dtype, device)
    if not encoder_matches(encoder, info):
        raise ValueError(
            f"This IP-Adapter needs the {ENCODER_NAMES[info.encoder]} image encoder, "
            f"but the selected image encoder is not that one"
        )
    try:
        embeds = encode_reference(image, encoder, info, device, dtype)
    finally:
        del encoder
    handle = inject_ip_adapter(unet, state, scale)
    try:
        yield [embeds]
    finally:
        remove_ip_adapter(unet, handle)
