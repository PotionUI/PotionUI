"""Hook points owned by the compute-provisioning domain."""

from src.platform.plugins.hooks import hooks_registry

COMPUTE_HOOKS = hooks_registry.declare(
    "compute", "backend",
    "register",  # Plugins register a ComputeProvisioner implementation
    specs={
        "register": {
            "description": "Fired at compute-provisioner registry init to let plugins register a ComputeProvisioner implementation.",
            "payload": {
                "provisioners": {"type": "Dict[str, type]", "description": "Map of provider_id -> ComputeProvisioner subclass, seeded empty"},
            },
            "mutable": ["provisioners"],
            "use_when": [
                "Register a compute-provisioning provider (appears in admin -> Remote Compute; used to provision/stop/terminate a GPU pod running the Remote Native worker)",
            ],
            "example": (
                "# manifest.yml\n"
                "hooks:\n"
                "  backend:\n"
                "    - hook: \"compute.register\"\n"
                "      handler: \"hooks.compute_hooks.register_provisioner\"\n\n"
                "# hooks/compute_hooks.py\n"
                "def register_provisioner(context: HookContext) -> HookContext:\n"
                "    context.data[\"provisioners\"][\"runpod\"] = RunpodComputeProvisioner\n"
                "    return context\n"
            ),
        },
    },
)
