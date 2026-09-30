
IMAGE = "fake/image-1"
VIDEO = "fake/video-1"


def suggested(env):
    return sorted(entry.provider_model_id for entry in env.repository.search("cloud-1", limit=100)[0] if entry.suggested)


async def test_a_refresh_marks_the_models_a_provider_suggests(fake_backend):
    fake_backend.backend().provider.suggested_model_ids = lambda: (IMAGE, "fake/not-offered")

    await fake_backend.catalog.refresh("cloud-1")

    assert suggested(fake_backend) == [IMAGE]


async def test_a_later_refresh_moves_the_mark_when_the_suggestions_change(fake_backend):
    provider = fake_backend.backend().provider
    provider.suggested_model_ids = lambda: (IMAGE,)
    await fake_backend.catalog.refresh("cloud-1")

    provider.suggested_model_ids = lambda: (VIDEO,)
    await fake_backend.catalog.refresh("cloud-1")

    assert suggested(fake_backend) == [VIDEO]


async def test_a_provider_without_suggestions_marks_nothing(fake_backend):
    await fake_backend.catalog.refresh("cloud-1")

    assert suggested(fake_backend) == []


async def test_suggested_models_are_not_enabled_by_the_mark(fake_backend):
    fake_backend.backend().provider.suggested_model_ids = lambda: (IMAGE,)

    await fake_backend.catalog.refresh("cloud-1")

    assert fake_backend.repository.list_enabled("cloud-1") == []
