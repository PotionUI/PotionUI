"""Registry of `ComputeProvisioner` implementations, collected via the
`compute.register` hook - mirrors `BackendRegistry._load_plugin_backends`:
built once at container-construction time (the plugin registry has already
run every enabled plugin's manifest by then), synchronously, not lazily
rediscovered per request.
"""

from typing import Dict, List, Optional

from src.platform.observability.logger import logger
from src.features.provisioning.contracts import ComputeProvisioner
from src.features.provisioning.hooks import COMPUTE_HOOKS


class ComputeProvisionerRegistry:
    """Central point for looking up a registered `ComputeProvisioner` by
    `provider_id`. Core names no provider - every entry comes from a plugin's
    `compute.register` hook handler."""

    def __init__(self, plugin_registry=None):
        self._plugin_registry = plugin_registry
        self._provisioners: Dict[str, ComputeProvisioner] = {}
        self._load_plugin_provisioners()

    def _load_plugin_provisioners(self) -> None:
        if not self._plugin_registry:
            logger.debug("[COMPUTE_PROVISIONER_REGISTRY] No plugin registry available, skipping discovery")
            return

        try:
            context, success = self._plugin_registry.execute_hook(
                COMPUTE_HOOKS.register, initial_data={"provisioners": {}}
            )
        except Exception as e:
            logger.error(f"[COMPUTE_PROVISIONER_REGISTRY] Failed to run compute.register hook: {e}")
            return

        if not success:
            logger.warning("[COMPUTE_PROVISIONER_REGISTRY] Some compute.register handlers failed")

        registered = context.data.get("provisioners", {}) if context else {}
        for provider_id, provisioner_class in registered.items():
            if not (isinstance(provisioner_class, type) and issubclass(provisioner_class, ComputeProvisioner)):
                logger.error(
                    f"[COMPUTE_PROVISIONER_REGISTRY] Provider '{provider_id}' does not subclass ComputeProvisioner"
                )
                continue
            try:
                self._provisioners[provider_id] = provisioner_class()
                logger.info(f"[COMPUTE_PROVISIONER_REGISTRY] Registered compute provisioner: {provider_id}")
            except Exception as e:
                logger.error(f"[COMPUTE_PROVISIONER_REGISTRY] Failed to instantiate provisioner '{provider_id}': {e}")

    def get(self, provider_id: str) -> Optional[ComputeProvisioner]:
        return self._provisioners.get(provider_id)

    def list_provisioners(self) -> List[ComputeProvisioner]:
        return list(self._provisioners.values())
