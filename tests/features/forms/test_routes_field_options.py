"""Tests for the REAL FormController.get_field_options (src.features.forms.routes) -
specifically that `current_user` is threaded through to FormManager.get_field_options
(closes POST /api/fields/options' unfiltered model listing).

tests/features/forms/test_routes.py exercises a parallel hand-rolled
FormController/MockFormManager pair (to sidestep an old circular-import
concern that no longer applies), so it never touches the real controller -
this file targets the real one directly.
"""
import pytest
from unittest.mock import Mock

from src.features.forms.routes import FormController


@pytest.fixture
def mock_form_manager():
    return Mock()


@pytest.fixture
def controller(mock_form_manager):
    return FormController(mock_form_manager)


@pytest.fixture
def current_user():
    user = Mock()
    user.id = "user-1"
    return user


class TestGetFieldOptionsThreadsCurrentUser:
    @pytest.mark.asyncio
    async def test_current_user_is_passed_to_the_manager(self, controller, mock_form_manager, current_user):
        mock_form_manager.get_field_options.return_value = []

        await controller.get_field_options("model", {"model_type": "checkpoint"}, current_user)

        mock_form_manager.get_field_options.assert_called_once_with(
            "model", {"model_type": "checkpoint"}, current_user
        )

    @pytest.mark.asyncio
    async def test_current_user_defaults_to_none(self, controller, mock_form_manager):
        """The controller can still be called without a user (tests, or a
        caller with no request context) - defaults to None, matching
        FormManager.get_field_options' own no-op-without-a-user contract."""
        mock_form_manager.get_field_options.return_value = []

        await controller.get_field_options("select", {"options": []})

        mock_form_manager.get_field_options.assert_called_once_with("select", {"options": []}, None)

    @pytest.mark.asyncio
    async def test_success_response_still_carries_options(self, controller, mock_form_manager, current_user):
        mock_form_manager.get_field_options.return_value = [{"label": "A", "value": "a"}]

        result = await controller.get_field_options("model", {}, current_user)

        assert result.success is True
        assert result.data == [{"label": "A", "value": "a"}]
