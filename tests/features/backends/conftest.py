import sys

import pytest


@pytest.fixture(autouse=True)
def _restore_text_encoder_placement():
    yield
    module = sys.modules.get("src.platform.runtime.native.memory.text_encoder")
    if module is not None:
        module.reset_text_encoder_placement()
