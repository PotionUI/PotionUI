"""Media index: system tags produced by a local auto-tagger, plus the
reusable pending-queue future index passes (e.g. CLIP embeddings) share."""

from src.features.media_index.manager import MediaIndexManager

__all__ = ["MediaIndexManager"]
