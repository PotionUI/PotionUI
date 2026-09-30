from src.features.cloud.testing.fake import FakeCloudConfig
from tests.features.cloud.conftest import CloudEnv


async def test_a_cloud_backend_runs_as_many_jobs_as_its_parallel_setting(cloud_env: CloudEnv):
    await cloud_env.registry.add_backend(FakeCloudConfig(id="cloud-3", name="Three", max_parallel=3))

    assert cloud_env.backend("cloud-3").max_concurrent_runs == 3


async def test_a_cloud_backend_runs_four_jobs_by_default(cloud_env: CloudEnv):
    backend = await cloud_env.add_backend("cloud-default")

    assert backend.max_concurrent_runs == 4
