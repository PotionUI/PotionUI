import ipaddress
import socket

_ALLOWED_NAMES = frozenset({"", "localhost"})
_INSTALLED_FLAG = "_potionui_network_guard"


def is_local_host(host) -> bool:
    if host is None:
        return True
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "replace")
    if not isinstance(host, str):
        return True
    name = host.strip().lower().split("%", 1)[0].rstrip(".")
    if name in _ALLOWED_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_loopback or ip.is_unspecified


def _is_ip_literal(host) -> bool:
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    try:
        ipaddress.ip_address(host.strip().split("%", 1)[0])
    except ValueError:
        return False
    return True


def _describe(address) -> str:
    if isinstance(address, tuple) and address:
        host = address[0]
        port = address[1] if len(address) > 1 else ""
        return f"{host}:{port}"
    return str(address)


def _check_address(address) -> None:
    if not isinstance(address, tuple) or not address:
        return
    if not is_local_host(address[0]):
        raise RuntimeError(f"Tests must not use the network: tried {_describe(address)}")


def install() -> None:
    if getattr(socket, _INSTALLED_FLAG, False):
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self, address):
        _check_address(address)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        _check_address(address)
        return real_connect_ex(self, address)

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        if not is_local_host(host) and not _is_ip_literal(host):
            raise RuntimeError(f"Tests must not use the network: tried {_describe((host, port))}")
        return real_getaddrinfo(host, port, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.getaddrinfo = guarded_getaddrinfo
    setattr(socket, _INSTALLED_FLAG, True)


install()
