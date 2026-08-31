"""Provisioning rented GPU compute.

A compute-provisioning plugin subclasses `ComputeProvisioner` to teach the
application how to provision, check on, stop and terminate a pod running the
Remote Native worker on one provider (RunPod, ...). Core owns no provider and
owns the `native.remote` backend row a successful `provision()` produces -
see `docs/remote-native.md` and the `runpod-provider` plugin.
"""

from src.features.provisioning.contracts import (
    ComputeFieldDescriptorV1,
    ComputeFieldOptionV1,
    ComputeProvisioner,
    ComputeProvisionerError,
    ComputeStatus,
    ProvisionRequest,
    ProvisionResult,
)
from src.features.provisioning.hooks import COMPUTE_HOOKS

__all__ = [
    "ComputeFieldDescriptorV1",
    "ComputeFieldOptionV1",
    "ComputeProvisioner",
    "ComputeProvisionerError",
    "ComputeStatus",
    "ProvisionRequest",
    "ProvisionResult",
    "COMPUTE_HOOKS",
]
