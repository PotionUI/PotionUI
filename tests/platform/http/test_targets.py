import socket

import pytest

from src.platform.http.targets import BlockedTargetError, PublicOnlyResolver, is_public_address, origin_of


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "100.64.0.1",
        "100.100.100.200",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "::ffff:100.100.100.200",
        "64:ff9b::7f00:1",
        "64:ff9b::a00:1",
        "2002:7f00:1::",
        "2002:a9fe:a9fe::",
        "224.0.0.1",
        "fe80::1%eth0",
    ],
)
def test_non_public_addresses_are_refused(address):
    assert is_public_address(address) is False


@pytest.mark.parametrize(
    "address",
    ["93.184.216.34", "8.8.8.8", "2606:2800:220:1:248:1893:25c8:1946", "::ffff:8.8.8.8", "64:ff9b::808:808", "2002:808:808::"],
)
def test_public_addresses_pass(address):
    assert is_public_address(address) is True


def test_origin_fills_default_ports_and_lowercases():
    assert origin_of("HTTPS://Example.com/x") == ("https", "example.com", 443)
    assert origin_of("http://example.com:8080/") == ("http", "example.com", 8080)


@pytest.mark.asyncio
async def test_resolver_pins_only_public_addresses():
    async def lookup(host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    records = await PublicOnlyResolver(False, lookup).resolve("example.com", 443)
    assert records[0]["host"] == "93.184.216.34"
    assert records[0]["flags"] == socket.AI_NUMERICHOST


@pytest.mark.asyncio
async def test_resolver_blocks_a_name_with_any_private_answer():
    async def lookup(host, port, type=0):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", port)),
        ]

    with pytest.raises(BlockedTargetError):
        await PublicOnlyResolver(False, lookup).resolve("rebind.example.com", 80)
    allowed = await PublicOnlyResolver(True, lookup).resolve("rebind.example.com", 80)
    assert len(allowed) == 2


@pytest.mark.asyncio
async def test_resolver_refuses_a_name_that_resolves_only_to_loopback():
    async def lookup(host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    with pytest.raises(BlockedTargetError, match="non-public address"):
        await PublicOnlyResolver(False, lookup).resolve("internal.example.com", 80)


@pytest.mark.asyncio
async def test_resolver_refuses_cgnat_answers():
    async def lookup(host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.100.100.200", port))]

    with pytest.raises(BlockedTargetError):
        await PublicOnlyResolver(False, lookup).resolve("meta.example.com", 80)
