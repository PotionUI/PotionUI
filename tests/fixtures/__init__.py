"""
Test fixtures package.

This package provides reusable test data and fixture functions for
various domain models and services.
"""

from tests.fixtures.generation_fixtures import *
from tests.fixtures.user_fixtures import *
from tests.fixtures.preset_fixtures import *
from tests.fixtures.image_fixtures import *

__all__ = [
    # Generation fixtures
    'sample_generation',
    'fake_form_data',
    'sample_generation_with_files',

    # User fixtures
    'sample_user',
    'sample_admin_user',

    # Preset fixtures
    'sample_preset_template',
    'sample_mode_template',
    'sample_form_template',

    # Image fixtures
    'fake_image',
    'fake_image_bytes',
]
