"""Registers this plugin's `ComputeProvisioner` with core's provisioning
registry (`src.features.provisioning.registry.ComputeProvisionerRegistry`)."""

import logging

logger = logging.getLogger(__name__)


def register_provisioner(context):
    from ..provisioner import RunpodComputeProvisioner

    context.data["provisioners"]["runpod"] = RunpodComputeProvisioner
    logger.info("[RUNPOD-PROVIDER] Registered RunPod compute provisioner")
    return context
