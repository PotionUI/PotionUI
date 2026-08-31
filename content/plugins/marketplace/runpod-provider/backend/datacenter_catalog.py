"""Static RunPod data-center catalog.

RunPod's REST API (v1) has no endpoint to list data centers. The ids below
are RunPod's own published set of active data centers (docs.runpod.io), not
derived from the REST API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class DataCenter:
    id: str
    geography: str


STATIC_DATACENTER_CATALOG: List[DataCenter] = [
    DataCenter("EU-RO-1", "Europe"),
    DataCenter("EU-SE-1", "Europe"),
    DataCenter("EU-CZ-1", "Europe"),
    DataCenter("EU-NL-1", "Europe"),
    DataCenter("US-CA-2", "US"),
    DataCenter("US-GA-1", "US"),
    DataCenter("US-GA-2", "US"),
    DataCenter("US-IL-1", "US"),
    DataCenter("US-KS-2", "US"),
    DataCenter("US-NC-1", "US"),
    DataCenter("US-TX-3", "US"),
    DataCenter("US-TX-4", "US"),
    DataCenter("US-WA-1", "US"),
    DataCenter("CA-MTL-1", "Canada"),
    DataCenter("CA-MTL-2", "Canada"),
    DataCenter("CA-MTL-3", "Canada"),
    DataCenter("AP-JP-1", "Asia-Pacific"),
    DataCenter("OC-AU-1", "Oceania"),
]
