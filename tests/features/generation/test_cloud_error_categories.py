import pytest

from src.features.generation.error_classification import ERROR_CATEGORIES, classify_generation_error
from src.features.generation.failure import failure_from_exception
from src.pipelines.cloud import CLOUD_ERROR_KINDS, CloudRunError


@pytest.mark.parametrize("kind", CLOUD_ERROR_KINDS)
def test_every_cloud_error_kind_has_its_own_category_with_a_plain_message(kind):
    classification = classify_generation_error(CloudRunError(kind, "raw provider text"))

    assert classification.category == f"cloud_{kind}"
    assert classification.category in ERROR_CATEGORIES
    assert classification.summary and classification.suggestions
    assert "raw provider text" not in classification.summary


def test_the_failure_keeps_the_category_and_a_user_message_without_provider_detail():
    failure = failure_from_exception(CloudRunError("credits", "out", detail="HTTP 402: account 42 empty"))

    assert failure.error_code == "cloud_credits"
    assert "HTTP 402" not in failure.user_message
