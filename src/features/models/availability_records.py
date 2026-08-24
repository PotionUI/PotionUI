from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ModelAvailability:
    """A claim that `backend_id` can load `model_id`, and the string it needs to do so.

    `ref` is engine-native and opaque to core: `models/loras/x.safetensors` for the
    native engine, `style/x.safetensors` for a ComfyUI server. `confidence` records how
    much the backend actually proved - see src/features/backends/model_listing.py.

    `digest` is the content sha256 THIS backend computed for its own copy of the file
    when it was indexed - not necessarily the same value as `models.sha256` (the
    model's canonical digest). A disagreement between the two is exactly what
    `confidence = CONFIDENCE_CONFLICT` records; see backend_indexer.py.
    """

    id: str
    model_id: str
    backend_id: str
    ref: str
    size: Optional[int] = None
    confidence: str = "reported"
    digest: Optional[str] = None
    indexed_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "ModelAvailability":
        indexed_at = row["indexed_at"]
        if isinstance(indexed_at, str):
            try:
                indexed_at = datetime.fromisoformat(indexed_at)
            except ValueError:
                indexed_at = None

        row_keys = row.keys()
        return cls(
            id=row["id"],
            model_id=row["model_id"],
            backend_id=row["backend_id"],
            ref=row["ref"],
            size=row["size"],
            confidence=row["confidence"],
            # Guarded for rows read before migration 110 in the same process.
            digest=row["digest"] if "digest" in row_keys else None,
            indexed_at=indexed_at,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "model_id": self.model_id,
            "backend_id": self.backend_id,
            "ref": self.ref,
            "size": self.size,
            "confidence": self.confidence,
            "digest": self.digest,
            "indexed_at": self.indexed_at.isoformat() if self.indexed_at else None,
        }
