from src.features.system_monitor.connection_hub import (
    REVOKED_CLOSE_CODE,
    MonitoringConnectionHub,
)


class Socket:
    def __init__(self):
        self.sent = []
        self.closed = []

    async def send_text(self, data):
        self.sent.append(data)

    async def close(self, code, reason):
        self.closed.append((code, reason))


async def test_a_connection_that_loses_access_is_closed_and_not_sent_to():
    hub = MonitoringConnectionHub()
    allowed = {"value": True}
    socket = Socket()
    hub.add_connection(socket, lambda: allowed["value"])

    await hub.broadcast({"n": 1})
    allowed["value"] = False
    await hub.broadcast({"n": 2})

    assert len(socket.sent) == 1
    assert [code for code, _ in socket.closed] == [REVOKED_CLOSE_CODE]
    assert not hub.has_connections()


async def test_revoking_one_connection_leaves_the_others_receiving():
    hub = MonitoringConnectionHub()
    kept, revoked, unchecked = Socket(), Socket(), Socket()
    hub.add_connection(kept, lambda: True)
    hub.add_connection(revoked, lambda: False)
    hub.add_connection(unchecked)

    await hub.broadcast({"n": 1})

    assert len(kept.sent) == 1 and len(unchecked.sent) == 1
    assert revoked.sent == [] and len(revoked.closed) == 1
    assert hub.connection_count() == 2


async def test_a_check_that_raises_counts_as_revoked():
    hub = MonitoringConnectionHub()
    socket = Socket()

    def broken():
        raise RuntimeError("repository unavailable")

    hub.add_connection(socket, broken)
    await hub.broadcast({"n": 1})

    assert socket.sent == []
    assert not hub.has_connections()


async def test_a_socket_that_fails_to_close_is_still_dropped():
    hub = MonitoringConnectionHub()

    class Gone(Socket):
        async def close(self, code, reason):
            raise RuntimeError("already closed")

    socket = Gone()
    hub.add_connection(socket, lambda: False)
    await hub.broadcast({"n": 1})

    assert socket.sent == []
    assert not hub.has_connections()
