from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.platform.runtime.native.detect.registry import arch_registry

Guidance = Literal["cfg", "embedded", "none"]

_REGISTRY_GUIDANCE: dict[str, Guidance] = {"cfg": "cfg", "embedded": "embedded", "none": "none", "flow_map": "none"}

@dataclass(frozen=True)
class FamilyCapabilities:
    prompt_weighting: bool
    negative_prompt: bool
    guidance: Guidance

_DECLARED: dict[str, tuple[bool, bool]] = {
    "flux1": (False, False),
    "flux2": (True, False),
    "flux3_klein_base": (False, True),
    "flux3_klein": (False, False),
    "moss_image": (False, True),
    "krea2_turbo": (True, True),
    "qwen_image21": (False, True),
    "qwen_image": (True, True),
    "wan_t2v_14b": (False, True),
    "wan_i2v_14b": (False, True),
    "wan22_i2v_14b": (False, True),
    "wan_ti2v_5b": (False, True),
    "ltxav": (False, True),
    "ltxv": (False, True),
    "anima": (True, True),
    "z_image": (True, True),
    "seedvr2_3b": (False, False),
    "seedvr2_7b": (False, False),
    "h3": (False, False),
    "h3_image": (False, False),
    "music3": (False, False),
    "yue2_3b": (False, False),
}

FAMILY_CAPABILITIES: dict[str, FamilyCapabilities] = {
    spec.variant: FamilyCapabilities(
        *_DECLARED[spec.variant], _REGISTRY_GUIDANCE[spec.sampling_settings["guidance"]]
    )
    for spec in arch_registry.all()
    if spec.variant in _DECLARED
}
