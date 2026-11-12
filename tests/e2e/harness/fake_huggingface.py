from __future__ import annotations

import http.server
import threading
import time
from typing import Dict


class FakeHuggingFace:
    def __init__(self, files: Dict[str, bytes], chunk_delay: float = 0.0, chunk_size: int = 65536):
        self.files = files
        self.requests: list = []
        self.range_requests: list = []
        self.chunk_delay = chunk_delay
        self.chunk_size = chunk_size
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def _serve(self, head: bool) -> None:
                owner.requests.append((self.command, self.path))
                if self.path.startswith("/api/"):
                    body = b"[]"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if not head:
                        self.wfile.write(body)
                    return
                key = self.path.split("?", 1)[0].lstrip("/")
                data = owner.files.get(key)
                if data is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = 0
                status = 200
                range_header = self.headers.get("Range")
                if range_header and range_header.startswith("bytes="):
                    start = int(range_header[6:].split("-", 1)[0] or 0)
                    status = 206
                    owner.range_requests.append((self.path, range_header))
                chunk = data[start:]
                self.send_response(status)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(chunk)))
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                self.end_headers()
                if not head:
                    if owner.chunk_delay <= 0:
                        self.wfile.write(chunk)
                    else:
                        for offset in range(0, len(chunk), owner.chunk_size):
                            self.wfile.write(chunk[offset : offset + owner.chunk_size])
                            self.wfile.flush()
                            time.sleep(owner.chunk_delay)

            def do_GET(self):
                self._serve(False)

            def do_HEAD(self):
                self._serve(True)

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
