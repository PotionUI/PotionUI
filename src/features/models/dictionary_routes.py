from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
import logging

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.features.models.indexer import ModelScanner

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)


class DictionaryController(BaseController):
    """Controller for dictionary/reference data endpoints"""

    def __init__(self):
        logger.debug("Initializing DictionaryController")
        super().__init__()
        self.router = APIRouter()
        self.setup_routes()
        logger.debug("DictionaryController initialized successfully")

    def setup_routes(self):
        """Setup API routes"""
        logger.debug("Setting up dictionary controller routes")

        @self.router.get(
            "/models",
            response_model=APIResponse,
            summary="List supported model types",
        )
        async def get_models_dictionary(current_user = Depends(get_current_active_user)):
            """
            Get available model types

            Returns a list of all supported model types from the system.
            These types correspond to the directory structure in the models folder.
            """
            try:
                # Get model types from the scanner's directory->type mapping
                model_types = list(ModelScanner.MODEL_TYPE_MAPPING.values())

                return self.success_response({
                    "models": model_types
                })

            except Exception as e:
                logger.error(f"Error getting models dictionary: {e}")
                return self.error_response(f"Failed to get models dictionary: {str(e)}")


def build_router(container: "AppContainer") -> APIRouter:
    return DictionaryController().router
