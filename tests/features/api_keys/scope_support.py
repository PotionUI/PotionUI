from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.http.base_controller import APIResponse


class Reply(APIResponse):
    def __await__(self):
        async def done():
            return self

        return done().__await__()


class Anything:
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Anything()

    def __call__(self, *args, **kwargs):
        return Reply(success=True, data={})

    def __iter__(self):
        return iter(())


def app_with(*builders, container=None):
    app = FastAPI()
    for build in builders:
        app.include_router(build(container if container is not None else Anything()))
    return TestClient(app, raise_server_exceptions=False)
