import asyncio
import socket
import threading

import aiohttp
import pytest
from aiohttp import web

import network_guard


class TestPublicDestinationsBlocked:
    def test_dns_lookup_of_public_host_raises(self):
        with pytest.raises(RuntimeError, match="Tests must not use the network"):
            socket.getaddrinfo("civitai.com", 443)

    def test_ip_literal_lookup_is_local_only_and_connect_is_still_blocked(self):
        assert socket.getaddrinfo("93.184.216.34", 443)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            with pytest.raises(RuntimeError, match="Tests must not use the network"):
                sock.connect(("93.184.216.34", 443))

    def test_connect_to_public_ip_raises(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            with pytest.raises(RuntimeError, match="Tests must not use the network"):
                sock.connect(("93.184.216.34", 80))

    def test_connect_ex_to_public_ip_raises(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            with pytest.raises(RuntimeError, match="Tests must not use the network"):
                sock.connect_ex(("93.184.216.34", 80))

    def test_ipv6_four_tuple_to_public_ip_raises(self):
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
            with pytest.raises(RuntimeError, match="Tests must not use the network"):
                sock.connect(("2606:4700:4700::1111", 443, 0, 0))

    def test_create_connection_to_public_host_raises(self):
        with pytest.raises(RuntimeError, match="Tests must not use the network"):
            socket.create_connection(("huggingface.co", 443), timeout=1)


class TestLoopbackAllowed:
    @pytest.mark.parametrize(
        "host",
        [None, "", "localhost", "127.0.0.1", "127.9.9.9", "::1", "::ffff:127.0.0.1", "0.0.0.0", b"localhost"],
    )
    def test_local_hosts_are_accepted(self, host):
        assert network_guard.is_local_host(host)

    @pytest.mark.parametrize("host", ["civitai.com", "8.8.8.8", "2606:4700:4700::1111", "127.0.0.1.evil.com"])
    def test_remote_hosts_are_rejected(self, host):
        assert not network_guard.is_local_host(host)

    def test_tcp_server_and_client_over_loopback(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        received = []

        def serve():
            conn, _ = server.accept()
            with conn:
                received.append(conn.recv(4))
                conn.sendall(b"pong")

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        with socket.create_connection(("localhost", port), timeout=5) as client:
            client.sendall(b"ping")
            assert client.recv(4) == b"pong"
        thread.join(timeout=5)
        server.close()
        assert received == [b"ping"]

    def test_socketpair_works(self):
        left, right = socket.socketpair()
        with left, right:
            left.sendall(b"x")
            assert right.recv(1) == b"x"

    @pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="no unix sockets")
    def test_unix_socket_path_is_not_checked(self, tmp_path):
        path = str(tmp_path / "s.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(path)
        server.close()

    async def test_aiohttp_local_server_and_client(self):
        async def hello(request):
            return web.json_response({"ok": True})

        app = web.Application()
        app.router.add_get("/", hello)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://localhost:{port}/") as response:
                    assert (await response.json()) == {"ok": True}
        finally:
            await runner.cleanup()

    async def test_asyncio_public_connection_raises(self):
        with pytest.raises(RuntimeError, match="Tests must not use the network"):
            await asyncio.open_connection("civitai.com", 443)
