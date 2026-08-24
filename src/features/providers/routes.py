"""
Provider Controller for managing marketplace providers.

This controller provides API endpoints for:
- Listing registered providers
- Getting provider settings schemas
- Updating provider settings
- Testing provider connections
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import TYPE_CHECKING, Any, Dict
import logging

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.features.providers.dto import ProviderSettingsUpdate, ProviderTestResult, ProviderInfo
from src.features.providers.registry import ensure_providers_discovered
from src.platform.plugins import PluginRegistry
from src.features.providers.hooks import PROVIDER_HOOKS

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)


class ProviderController(BaseController):
    """Controller for marketplace provider management operations."""

    def __init__(self, plugin_registry: PluginRegistry):
        super().__init__()
        self.plugins = plugin_registry

    async def list_providers(self, current_user) -> APIResponse:
        """
        List all registered marketplace providers.

        Returns metadata for all discovered providers, including their
        capabilities and initialization status.
        """
        service = await ensure_providers_discovered()
        providers = service.get_all_provider_metadata()

        result = []
        for metadata in providers:
            result.append({
                "id": metadata.id,
                "name": metadata.name,
                "description": metadata.description,
                "website": metadata.website,
                "capabilities": [cap.name for cap in metadata.capabilities],
                "version": metadata.version,
                "initialized": service.is_provider_initialized(metadata.id),
                "icon": metadata.icon,
            })

        return self.success_response(data=result)

    async def get_provider(self, provider_id: str, current_user) -> ProviderInfo:
        """
        Get details for a specific provider.

        Args:
            provider_id: ID of the provider
            current_user: Currently authenticated user
        """
        try:
            service = await ensure_providers_discovered()
            metadata = service.get_provider_metadata(provider_id)

            if not metadata:
                raise HTTPException(status_code=404, detail=f"Provider not found: {provider_id}")

            return ProviderInfo(
                id=metadata.id,
                name=metadata.name,
                description=metadata.description,
                website=metadata.website,
                capabilities=[cap.name for cap in metadata.capabilities],
                version=metadata.version,
                initialized=service.is_provider_initialized(metadata.id),
                icon=metadata.icon,
            )

        except HTTPException:
            raise

    async def get_settings_schema(self, provider_id: str, current_user) -> Dict[str, Any]:
        """
        Get the JSON schema for a provider's settings.

        This schema can be used to generate settings forms in the UI.

        Args:
            provider_id: ID of the provider
            current_user: Currently authenticated user
        """
        try:
            service = await ensure_providers_discovered()
            schema = service.get_provider_settings_schema(provider_id)

            if schema is None:
                raise HTTPException(status_code=404, detail=f"Provider not found: {provider_id}")

            return schema

        except HTTPException:
            raise

    async def get_settings(self, provider_id: str, current_user) -> Dict[str, Any]:
        """
        Get current settings for a provider.

        Sensitive values (like API keys) are masked.

        Args:
            provider_id: ID of the provider
            current_user: Currently authenticated user
        """
        try:
            service = await ensure_providers_discovered()

            # Check provider exists
            if service.get_provider_metadata(provider_id) is None:
                raise HTTPException(status_code=404, detail=f"Provider not found: {provider_id}")

            settings = service.get_provider_current_settings(
                provider_id,
                user_id=current_user.id
            )

            return settings

        except HTTPException:
            raise

    async def update_settings(
        self,
        provider_id: str,
        request: ProviderSettingsUpdate,
        current_user
    ) -> Dict[str, str]:
        """
        Update settings for a provider.

        After updating settings, the provider will be re-initialized
        with the new configuration.

        Args:
            provider_id: ID of the provider
            request: New settings to apply
            current_user: Currently authenticated user
        """
        try:
            service = await ensure_providers_discovered()

            # Check provider exists
            if service.get_provider_metadata(provider_id) is None:
                raise HTTPException(status_code=404, detail=f"Provider not found: {provider_id}")

            # Execute before hook
            self.plugins.execute_hook(
                PROVIDER_HOOKS.before_settings_update,
                initial_data={
                    "provider_id": provider_id,
                    "settings": request.settings,
                    "user_id": current_user.id
                }
            )

            success = await service.update_provider_settings(
                provider_id,
                request.settings,
                user_id=current_user.id
            )

            if not success:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to update provider settings"
                )

            # Execute after hook
            self.plugins.execute_hook(
                PROVIDER_HOOKS.after_settings_update,
                initial_data={
                    "provider_id": provider_id,
                    "settings": request.settings,
                    "user_id": current_user.id,
                    "success": success
                }
            )

            return {"message": "Settings updated successfully"}

        except HTTPException:
            raise

    async def test_connection(self, provider_id: str, current_user) -> ProviderTestResult:
        """
        Test connection to a provider.

        Verifies that the provider is properly configured and can
        connect to its API.

        Args:
            provider_id: ID of the provider
            current_user: Currently authenticated user
        """
        try:
            service = await ensure_providers_discovered()

            # Check provider exists
            metadata = service.get_provider_metadata(provider_id)
            if metadata is None:
                raise HTTPException(status_code=404, detail=f"Provider not found: {provider_id}")

            # Check if initialized
            if not service.is_provider_initialized(provider_id):
                return ProviderTestResult(
                    success=False,
                    message="Provider is not initialized. Please configure settings first."
                )

            # Test connection
            success = await service.test_provider_connection(provider_id)

            if success:
                return ProviderTestResult(
                    success=True,
                    message=f"Successfully connected to {metadata.name}"
                )
            else:
                return ProviderTestResult(
                    success=False,
                    message=f"Failed to connect to {metadata.name}. Please check your settings."
                )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error testing connection for {provider_id}: {e}")
            return ProviderTestResult(
                success=False,
                message=f"Connection test failed: {str(e)}"
            )

    async def initialize_provider(self, provider_id: str, current_user) -> Dict[str, str]:
        """
        Initialize a provider with current settings.

        This is useful after updating settings to apply changes
        without restarting the application.

        Args:
            provider_id: ID of the provider
            current_user: Currently authenticated user
        """
        try:
            service = await ensure_providers_discovered()

            # Check provider exists
            if service.get_provider_metadata(provider_id) is None:
                raise HTTPException(status_code=404, detail=f"Provider not found: {provider_id}")

            # Execute before hook
            self.plugins.execute_hook(
                PROVIDER_HOOKS.before_initialize,
                initial_data={
                    "provider_id": provider_id,
                    "user_id": current_user.id
                }
            )

            # Re-initialize
            results = await service.initialize_providers()
            success = results.get(provider_id, False)

            # Execute after hook
            self.plugins.execute_hook(
                PROVIDER_HOOKS.after_initialize,
                initial_data={
                    "provider_id": provider_id,
                    "user_id": current_user.id,
                    "success": success
                }
            )

            if success:
                return {"message": f"Provider {provider_id} initialized successfully"}
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to initialize provider {provider_id}"
                )

        except HTTPException:
            raise

    async def lookup_model_by_hash(
        self,
        provider_id: str,
        sha256: str,
        current_user
    ) -> Dict[str, Any]:
        """
        Look up model information by SHA256 hash.

        Args:
            provider_id: ID of the provider to use
            sha256: SHA256 hash of the model file
            current_user: Currently authenticated user
        """
        try:
            service = await ensure_providers_discovered()

            # Check provider exists and is initialized
            metadata = service.get_provider_metadata(provider_id)
            if metadata is None:
                raise HTTPException(status_code=404, detail=f"Provider not found: {provider_id}")

            if not service.is_provider_initialized(provider_id):
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider {provider_id} is not initialized"
                )

            # Lookup model
            model_info = await service.get_model_by_hash(provider_id, sha256)

            if model_info is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Model not found on {metadata.name} for hash: {sha256}"
                )

            return model_info.to_dict()

        except HTTPException:
            raise


def build_router(container: "AppContainer") -> APIRouter:
    controller = ProviderController(container.plugin_registry)
    router = APIRouter(prefix="/api/providers", tags=["Providers"])

    @router.get("/", response_model=APIResponse, summary="List marketplace providers")
    async def list_providers(current_user=Depends(get_current_active_user)):
        """List all registered marketplace providers."""
        return await controller.list_providers(current_user)

    @router.get("/{provider_id}", response_model=ProviderInfo, summary="Get a provider's details")
    async def get_provider(provider_id: str, current_user=Depends(get_current_active_user)):
        """Get details for a specific provider."""
        return await controller.get_provider(provider_id, current_user)

    @router.get("/{provider_id}/settings/schema", summary="Get a provider's settings schema")
    async def get_provider_settings_schema(
        provider_id: str,
        current_user=Depends(get_current_active_user)
    ):
        """Get the JSON schema for a provider's settings."""
        return await controller.get_settings_schema(provider_id, current_user)

    @router.get("/{provider_id}/settings", summary="Get a provider's current settings")
    async def get_provider_settings(
        provider_id: str,
        current_user=Depends(get_current_active_user)
    ):
        """Get current settings for a provider."""
        return await controller.get_settings(provider_id, current_user)

    @router.put("/{provider_id}/settings", summary="Update a provider's settings")
    async def update_provider_settings(
        provider_id: str,
        request: ProviderSettingsUpdate,
        current_user=Depends(get_current_active_user)
    ):
        """Update settings for a provider."""
        return await controller.update_settings(provider_id, request, current_user)

    @router.post("/{provider_id}/test", response_model=ProviderTestResult, summary="Test a provider connection")
    async def test_provider_connection(
        provider_id: str,
        current_user=Depends(get_current_active_user)
    ):
        """Test connection to a provider."""
        return await controller.test_connection(provider_id, current_user)

    @router.post("/{provider_id}/initialize", summary="Initialize a provider")
    async def initialize_provider(
        provider_id: str,
        current_user=Depends(get_current_admin_user)
    ):
        """Initialize a provider with current settings."""
        return await controller.initialize_provider(provider_id, current_user)

    @router.get("/{provider_id}/lookup/{sha256}", summary="Look up a model by file hash")
    async def lookup_model_by_hash(
        provider_id: str,
        sha256: str,
        current_user=Depends(get_current_active_user)
    ):
        """Look up model information by SHA256 hash."""
        return await controller.lookup_model_by_hash(provider_id, sha256, current_user)

    return router
