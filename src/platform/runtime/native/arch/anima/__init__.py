"""Anima DiT arch package (MiniTrainDIT backbone + in-model LLMAdapter)."""

from .config import AnimaConfig
from .model import Anima

__all__ = ["Anima", "AnimaConfig"]
