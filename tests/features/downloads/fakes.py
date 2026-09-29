import asyncio


class FakeResponse:
    def __init__(self, status=200, chunks=(b"abcd", b"efgh"), headers=None, fail_after=None):
        self.status = status
        self.reason = "OK"
        self.headers = dict(headers or {})
        self.content = self
        self._chunks = list(chunks)
        self._fail_after = fail_after

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def iter_chunked(self, size):
        for index, chunk in enumerate(self._chunks):
            await asyncio.sleep(0)
            if self._fail_after is not None and index >= self._fail_after:
                raise ConnectionError("interrupted")
            yield chunk


class FakeSession:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests = []

    def get(self, url, headers=None, **kwargs):
        self.requests.append((url, dict(headers or {})))
        return self._responses.pop(0)
