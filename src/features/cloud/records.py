from dataclasses import dataclass, field
from typing import Any, Optional

from src.features.cloud.contracts import CloudModelSpec
from src.features.cloud.spec_codec import spec_from_json
from src.platform.database.rows import json_column, row_get


@dataclass
class CloudCatalogEntry:
    backend_id: str
    slug: str
    provider_model_id: str
    label: str
    vendor: Optional[str]
    tasks: list[str]
    outputs: list[str]
    spec_json: str
    enabled: bool
    suggested: bool
    deprecated_at: Optional[str]
    discovered_at: str
    refreshed_at: str
    missing_since: Optional[str]
    enabled_at: Optional[str]
    model_id: Optional[str] = None
    _spec: Optional[CloudModelSpec] = field(default=None, repr=False, compare=False)

    @property
    def spec(self) -> CloudModelSpec:
        if self._spec is None:
            self._spec = spec_from_json(self.spec_json)
        return self._spec

    @property
    def available(self) -> bool:
        return self.missing_since is None

    @classmethod
    def from_row(cls, row: Any) -> "CloudCatalogEntry":
        return cls(
            backend_id=row["backend_id"],
            slug=row["slug"],
            provider_model_id=row["provider_model_id"],
            label=row["label"],
            vendor=row["vendor"],
            tasks=list(json_column(row["tasks"], [])),
            outputs=list(json_column(row["outputs"], [])),
            spec_json=row["spec"],
            enabled=bool(row["enabled"]),
            suggested=bool(row["suggested"]),
            deprecated_at=row["deprecated_at"],
            discovered_at=row["discovered_at"],
            refreshed_at=row["refreshed_at"],
            missing_since=row["missing_since"],
            enabled_at=row["enabled_at"],
            model_id=row_get(row, "model_id"),
        )


@dataclass(frozen=True)
class CloudCatalogState:
    backend_id: str
    refreshed_at: str
    listed: int
    skipped: list[dict[str, Any]]

    @classmethod
    def from_row(cls, row: Any) -> "CloudCatalogState":
        return cls(
            backend_id=row["backend_id"],
            refreshed_at=row["refreshed_at"],
            listed=row["listed"],
            skipped=list(json_column(row["skipped"], [])),
        )
