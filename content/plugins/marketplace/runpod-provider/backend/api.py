"""RunPod Provider plugin API: validate an API key before it's saved.

Mounted at `/api/plugins/runpod-provider` by `PluginRouterMounter` from the
`api:` section of `manifest.yml`. Admin-only.

Provisioning (GPU types, provision/status/stop/terminate) goes through
core's `/api/admin/provisioning` routes (`src.features.provisioning.routes`),
which dispatch to `RunpodComputeProvisioner` (`backend/provisioner.py`),
registered via the `compute.register` hook.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.plugin_api import get_current_admin_user

from .client import RunPodAPIError, RunPodClient

router = APIRouter(
    prefix="/api/plugins/runpod-provider",
    tags=["RunPod Provider"],
    dependencies=[Depends(get_current_admin_user)],
)


class ValidateKeyRequest(BaseModel):
    api_key: str


class ValidateKeyResponse(BaseModel):
    valid: bool


@router.post("/validate-key", response_model=ValidateKeyResponse)
async def validate_key(payload: ValidateKeyRequest) -> ValidateKeyResponse:
    client = RunPodClient(api_key=payload.api_key)
    try:
        valid = await client.validate_api_key()
    except RunPodAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await client.aclose()
    return ValidateKeyResponse(valid=valid)
