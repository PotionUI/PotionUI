"""Wan 2.1 / 2.2 video diffusion transformer (base t2v / i2v backbone)."""

from .config import WanParams
from .model import WanModel

__all__ = ["WanModel", "WanParams"]
