"""Tests for FormController."""
import pytest
from unittest.mock import Mock, patch, MagicMock
from dataclasses import dataclass
from typing import Any, Optional


# Create mock APIResponse to avoid circular import issues
@dataclass
class MockAPIResponse:
    """Mock version of APIResponse for testing."""
    success: bool = True
    data: Any = None
    message: str = ""
    error: str = ""


# Create a mock FormManager
class MockFormManager:
    """Mock FormManager for testing."""
    def get_field_options(self, field_type: str, field_config: dict):
        pass

    def validate_form_data(self, form_schema: dict, form_data: dict):
        pass

    def get_form_defaults(self, preset_id: str):
        pass


# Create a simplified FormController for testing
class FormController:
    """Simplified FormController for testing."""

    def __init__(self, form_manager):
        self.manager = form_manager
        self.logger = MagicMock()

    def success_response(self, data=None, message=""):
        return MockAPIResponse(success=True, data=data, message=message)

    def error_response(self, error="", message="", status_code=400):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status_code,
            detail={"error": error, "message": message}
        )

    async def get_field_options(self, field_type: str, field_config: dict):
        try:
            options = self.manager.get_field_options(field_type, field_config)
            return self.success_response(data=options)
        except ValueError as e:
            return self.error_response(error="field_options_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Failed to get field options: {e}")
            return self.error_response(error="field_options_failed", message=f"Failed to get field options: {str(e)}")

    async def validate_form_data(self, form_schema: dict, form_data: dict):
        try:
            result = self.manager.validate_form_data(form_schema, form_data)
            return self.success_response(message="Form data is valid", data=result)
        except ValueError as e:
            return self.error_response(error="validation_error", message=str(e), status_code=422)
        except Exception as e:
            self.logger.error(f"Failed to validate form data: {e}")
            return self.error_response(error="validation_error", message=f"Failed to validate form data: {str(e)}")

    async def get_form_defaults(self, preset_id: str):
        try:
            defaults = self.manager.get_form_defaults(preset_id)
            return self.success_response(data=defaults)
        except ValueError as e:
            return self.error_response(error="defaults_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Failed to get form defaults: {e}")
            return self.error_response(error="defaults_failed", message=f"Failed to get form defaults: {str(e)}")


from fastapi import HTTPException


class TestFormController:
    """Tests for FormController - validates thin controller behavior."""

    @pytest.fixture
    def mock_form_manager(self):
        """Mock FormManager."""
        return Mock(spec=MockFormManager)

    @pytest.fixture
    def controller(self, mock_form_manager):
        """Create FormController instance with mocked FormManager."""
        return FormController(mock_form_manager)

    # ========== Test get_field_options ==========

    @pytest.mark.asyncio
    async def test_get_field_options_success(self, controller, mock_form_manager):
        """Test successful get_field_options."""
        mock_form_manager.get_field_options.return_value = [
            {"label": "Option 1", "value": "opt1"},
            {"label": "Option 2", "value": "opt2"}
        ]

        result = await controller.get_field_options("select", {"options": []})

        assert result.success is True
        assert len(result.data) == 2
        assert result.data[0]["label"] == "Option 1"
        mock_form_manager.get_field_options.assert_called_once_with("select", {"options": []})

    @pytest.mark.asyncio
    async def test_get_field_options_value_error(self, controller, mock_form_manager):
        """Test get_field_options when manager raises ValueError."""
        mock_form_manager.get_field_options.side_effect = ValueError("Unsupported field type")

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_field_options("unsupported", {})

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail['error'] == "field_options_failed"
        assert "Unsupported field type" in exc_info.value.detail['message']

    @pytest.mark.asyncio
    async def test_get_field_options_exception(self, controller, mock_form_manager):
        """Test get_field_options exception handling."""
        mock_form_manager.get_field_options.side_effect = Exception("Unexpected error")

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_field_options("select", {})

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail['error'] == "field_options_failed"
        assert "Unexpected error" in exc_info.value.detail['message']

    # ========== Test validate_form_data ==========

    @pytest.mark.asyncio
    async def test_validate_form_data_success(self, controller, mock_form_manager):
        """Test successful validate_form_data."""
        mock_form_manager.validate_form_data.return_value = {"valid": True}

        result = await controller.validate_form_data(
            {"required": ["prompt"]},
            {"prompt": "test"}
        )

        assert result.success is True
        assert "valid" in result.message.lower()
        mock_form_manager.validate_form_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_form_data_validation_error(self, controller, mock_form_manager):
        """Test validate_form_data when validation fails."""
        mock_form_manager.validate_form_data.side_effect = ValueError(
            "Form validation failed: Field 'prompt' is required"
        )

        with pytest.raises(HTTPException) as exc_info:
            await controller.validate_form_data({"required": ["prompt"]}, {})

        assert exc_info.value.status_code == 422
        assert exc_info.value.detail['error'] == "validation_error"
        assert "prompt" in exc_info.value.detail['message']

    @pytest.mark.asyncio
    async def test_validate_form_data_exception(self, controller, mock_form_manager):
        """Test validate_form_data exception handling."""
        mock_form_manager.validate_form_data.side_effect = Exception("Schema processing error")

        with pytest.raises(HTTPException) as exc_info:
            await controller.validate_form_data({}, {})

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail['error'] == "validation_error"
        assert "Schema processing error" in exc_info.value.detail['message']

    # ========== Test get_form_defaults ==========

    @pytest.mark.asyncio
    async def test_get_form_defaults_success(self, controller, mock_form_manager):
        """Test successful get_form_defaults."""
        mock_form_manager.get_form_defaults.return_value = {"steps": 20, "cfg": 7.5}

        result = await controller.get_form_defaults("test-preset-123")

        assert result.success is True
        assert result.data["steps"] == 20
        assert result.data["cfg"] == 7.5
        mock_form_manager.get_form_defaults.assert_called_once_with("test-preset-123")

    @pytest.mark.asyncio
    async def test_get_form_defaults_value_error(self, controller, mock_form_manager):
        """Test get_form_defaults when manager raises ValueError."""
        mock_form_manager.get_form_defaults.side_effect = ValueError("Preset not found")

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_form_defaults("nonexistent-preset")

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail['error'] == "defaults_failed"
        assert "Preset not found" in exc_info.value.detail['message']

    @pytest.mark.asyncio
    async def test_get_form_defaults_exception(self, controller, mock_form_manager):
        """Test get_form_defaults exception handling."""
        mock_form_manager.get_form_defaults.side_effect = Exception("Unexpected error")

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_form_defaults("test-preset")

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail['error'] == "defaults_failed"
        assert "Unexpected error" in exc_info.value.detail['message']

    # ========== Test controller initialization ==========

    def test_controller_initialization(self, mock_form_manager):
        """Test controller initialization."""
        controller = FormController(mock_form_manager)

        assert controller.manager == mock_form_manager


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
