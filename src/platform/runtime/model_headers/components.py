from __future__ import annotations

from dataclasses import dataclass

from .reader import HeaderView
from .signatures import FamilyMatch

DENOISER_MARKER = "model.diffusion_model."
VAE_PREFIXES = ("first_stage_model.", "vae.", "audio_vae.", "vocoder.")
TEXT_ENCODER_PREFIXES = (
    "conditioner.embedders.",
    "cond_stage_model.",
    "text_encoders.",
    "text_encoder.",
    "text_encoder_2.",
    "text_encoder_3.",
)
MIN_COMPONENT_TENSORS = 2
UNKNOWN_FAMILY = "unknown"

CHECKPOINT = "checkpoint"
DIFFUSION_MODEL = "diffusion_model"


@dataclass(frozen=True)
class Components:
    denoiser: bool
    vae: bool
    text_encoder: bool


@dataclass(frozen=True)
class Verdict:
    decided: bool
    model_type: str | None = None
    family: str | None = None
    variant: str | None = None
    classifier: str | None = None
    transformer_extractable: bool = False
    components: Components = Components(False, False, False)
    fingerprint: str = ""


def detect_components(view: HeaderView, match: FamilyMatch | None) -> Components:
    vae = 0
    text_encoder = 0
    marker = False
    for key in view.keys:
        if key.startswith(DENOISER_MARKER):
            marker = True
        elif key.startswith(VAE_PREFIXES):
            vae += 1
        elif key.startswith(TEXT_ENCODER_PREFIXES):
            text_encoder += 1
    return Components(
        denoiser=match is not None or marker,
        vae=vae >= MIN_COMPONENT_TENSORS,
        text_encoder=text_encoder >= MIN_COMPONENT_TENSORS,
    )


def decide(view: HeaderView, match: FamilyMatch | None, classifier: str | None = None, fingerprint: str = "") -> Verdict:
    components = detect_components(view, match)
    if not components.denoiser:
        return Verdict(False, components=components, fingerprint=fingerprint)

    bundled = components.vae or components.text_encoder
    if match is None:
        if not bundled:
            return Verdict(False, components=components, fingerprint=fingerprint)
        return Verdict(
            True,
            CHECKPOINT,
            UNKNOWN_FAMILY,
            components=components,
            fingerprint=fingerprint,
        )

    model_type = match.model_type or (CHECKPOINT if bundled else DIFFUSION_MODEL)
    return Verdict(
        True,
        model_type,
        match.family,
        match.variant,
        classifier,
        match.transformer_extractable,
        components,
        fingerprint,
    )
