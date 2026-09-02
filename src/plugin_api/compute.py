"""Provisioning rented GPU compute.

A compute-provisioning plugin subclasses `ComputeProvisioner` to teach the
application how to provision, check on, stop and terminate a pod running the
Remote Native worker on one provider (RunPod, ...). Core owns no provider and
owns the `native.remote` backend row a successful `provision()` produces -
see `docs/remote-native.md` and the `runpod-provider` plugin.
"""

from src.features.provisioning.contracts import (
    COMPUTE_STATES,
    STAGE_CREATING,
    STAGE_PREPARING,
    STAGE_READY,
    STAGE_STARTING,
    STAGE_WAITING_WORKER,
    STATE_FAILED,
    STATE_MISSING,
    STATE_PROVISIONING,
    STATE_RUNNING,
    STATE_STOPPED,
    STATE_UNKNOWN,
    STATE_UNREACHABLE,
    ComputeFieldDescriptorV1,
    ComputeFieldOptionV1,
    ComputeProvisioner,
    ComputeProvisionerError,
    ComputeStatus,
    ProgressReporter,
    ProvisionProgress,
    ProvisionRequest,
    ProvisionResult,
)
from src.features.provisioning.hooks import COMPUTE_HOOKS

__all__ = [
    "COMPUTE_STATES",
    "STAGE_CREATING",
    "STAGE_PREPARING",
    "STAGE_READY",
    "STAGE_STARTING",
    "STAGE_WAITING_WORKER",
    "STATE_FAILED",
    "STATE_MISSING",
    "STATE_PROVISIONING",
    "STATE_RUNNING",
    "STATE_STOPPED",
    "STATE_UNKNOWN",
    "STATE_UNREACHABLE",
    "ComputeFieldDescriptorV1",
    "ComputeFieldOptionV1",
    "ComputeProvisioner",
    "ComputeProvisionerError",
    "ComputeStatus",
    "ProgressReporter",
    "ProvisionProgress",
    "ProvisionRequest",
    "ProvisionResult",
    "COMPUTE_HOOKS",
]
