from src.features.cloud.contracts import CloudError

IMAGE = "fake/image-1"


def states(env):
    entries, _ = env.repository.search("cloud-1", limit=100)
    return sorted((entry.provider_model_id, entry.enabled, entry.available) for entry in entries)


async def test_a_refresh_whose_discovery_fails_leaves_every_model_as_it_was(fake_backend):
    await fake_backend.catalog.refresh("cloud-1")
    await fake_backend.catalog.set_enabled("cloud-1", [fake_backend.slug_of(IMAGE)], True)
    before = states(fake_backend)

    async def broken():
        raise CloudError("unavailable", "The provider is temporarily unavailable.")

    fake_backend.backend().provider.discover = broken

    try:
        await fake_backend.catalog.refresh("cloud-1")
    except CloudError:
        pass

    assert states(fake_backend) == before and all(available for _, _, available in before)
