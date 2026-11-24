from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Guidance = Literal["cfg", "embedded", "none"]


@dataclass(frozen=True)
class FamilyCapabilities:
    prompt_weighting: bool
    negative_prompt: bool
    guidance: Guidance


def _caps(prompt_weighting: bool, negative_prompt: bool, guidance: Guidance) -> FamilyCapabilities:
    return FamilyCapabilities(prompt_weighting, negative_prompt, guidance)


FAMILY_CAPABILITIES: dict[str, FamilyCapabilities] = {
    "flux1": _caps(False, False, "embedded"),
    "flux2": _caps(True, False, "none"),
    "flux3_klein_base": _caps(False, True, "cfg"),
    "flux3_klein": _caps(False, False, "none"),
    "moss_image": _caps(False, True, "cfg"),
    "krea2_turbo": _caps(True, True, "cfg"),
    "qwen_image21": _caps(False, True, "cfg"),
    "qwen_image": _caps(True, True, "cfg"),
    "wan_t2v_14b": _caps(False, True, "cfg"),
    "wan_i2v_14b": _caps(False, True, "cfg"),
    "wan22_i2v_14b": _caps(False, True, "cfg"),
    "wan_ti2v_5b": _caps(False, True, "cfg"),
    "ltxav": _caps(False, True, "cfg"),
    "ltxv": _caps(False, True, "cfg"),
    "anima": _caps(True, True, "cfg"),
    "z_image": _caps(True, True, "cfg"),
    "seedvr2_3b": _caps(False, False, "none"),
    "seedvr2_7b": _caps(False, False, "none"),
    "h3": _caps(False, False, "none"),
    "h3_image": _caps(False, False, "none"),
    "music3": _caps(False, False, "cfg"),
    "yue2_3b": _caps(False, False, "cfg"),
}
