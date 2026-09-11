"""Streaming an already-built zip archive out of a `SpooledTemporaryFile`
without ever holding it fully in memory as bytes.

Generic HTTP-response machinery - carries no assumption about what the zip
contains or how it was built, so any feature that hands back a
`(SpooledTemporaryFile, filename)` pair from its own export path can stream it
through `stream_spooled_zip` the same way.
"""
import io

from fastapi.responses import StreamingResponse

# Keeps a single read() from pulling the whole archive back into memory at
# once even though the file itself is already bounded.
DEFAULT_STREAM_CHUNK_BYTES = 1024 * 1024


def close_spooled_file(spooled_file) -> None:
    """Idempotent close, safe to call from more than one exit path."""
    if not spooled_file.closed:
        spooled_file.close()


class SpooledZipResponse(StreamingResponse):
    """A `StreamingResponse` that is the sole, explicit owner of closing its
    backing spooled temp file - on normal completion, a client disconnect, or
    an exception raised from the ASGI `send()` itself (e.g. a broken pipe).

    Starlette's `StreamingResponse.__call__` only awaits `background` after
    its internal task group exits *without* an exception - a disconnect is a
    self-cancellation the task group swallows, so `background` still runs,
    but a real exception from `send()` propagates straight out of `__call__`
    past that line, and `background` never runs at all. Wrapping `__call__`
    itself in `finally` closes the file on every exit path, with exactly one
    place responsible for it.
    """

    def __init__(self, zip_file, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._zip_file = zip_file

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            close_spooled_file(self._zip_file)


def stream_spooled_zip(
    zip_file, filename: str, chunk_size: int = DEFAULT_STREAM_CHUNK_BYTES
) -> SpooledZipResponse:
    """Wrap a `SpooledTemporaryFile` seeked to 0 into a streamed zip download.

    The returned response is the sole owner of closing `zip_file` from here on
    (which also removes its backing temp file, if the export rolled over to
    disk) - until it is actually returned, ownership hasn't transferred yet,
    so any failure while measuring or constructing the response here closes
    `zip_file` itself rather than leaving it ownerless.
    """
    try:
        zip_file.seek(0, io.SEEK_END)
        content_length = zip_file.tell()
        zip_file.seek(0)

        def iter_chunks():
            while True:
                chunk = zip_file.read(chunk_size)
                if not chunk:
                    break
                yield chunk

        return SpooledZipResponse(
            zip_file,
            iter_chunks(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(content_length),
            },
        )
    except Exception:
        close_spooled_file(zip_file)
        raise
