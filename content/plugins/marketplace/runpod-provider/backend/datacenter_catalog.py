"""A static RunPod data-center catalog.

RunPod's REST API (v1) has no endpoint to list data centers - the same
honest-absence situation `gpu_catalog.py` documents (verified the same way:
enumerating every path in `https://rest.runpod.io/v1/openapi.json`, fetched
2026-08-15 - no `/datacenters` or equivalent exists). The ids below are
RunPod's own published set of active data centers (docs.runpod.io), not
derived from the REST API, so this list drifts as RunPod adds or retires
data centers faster than this file gets updated.

`dataCenterId` is a REQUIRED field on `POST /networkvolumes` - RunPod rejects
an absent *or empty-string* value, which is exactly what reading it from the
plugin's own `region` setting used to produce on an install where that
optional setting was never set (see `provisioner.py`). The admin-facing field
is a closed select over this catalog, not free text; if RunPod ships a data
center not yet listed here, the `region` plugin setting is still read as a
fallback for a caller that omits `data_center_id` entirely (never for one
that submits it empty) - see `provisioner.provision()`.
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
