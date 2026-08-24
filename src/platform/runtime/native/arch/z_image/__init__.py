"""Z-Image NextDiT arch package (Lumina-Image-2.0 backbone, dim 3840)."""

from .config import ZImageConfig
from .model import ZImageDiT

__all__ = ["ZImageConfig", "ZImageDiT"]
