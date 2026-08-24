"""Vendored Flux1 / Flux2 (Klein) diffusion-transformer architecture."""

from __future__ import annotations

from .config import FluxParams
from .model import Flux

__all__ = ["Flux", "FluxParams"]
