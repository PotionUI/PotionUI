import functools
import logging
import os
import ssl
import sys

import aiohttp
import certifi

logger = logging.getLogger(__name__)

_OS_STORE_PLATFORMS = ("win32", "darwin")


@functools.lru_cache(maxsize=1)
def client_ssl_context() -> ssl.SSLContext:
    cafile = os.environ.get("SSL_CERT_FILE") or None
    capath = os.environ.get("SSL_CERT_DIR") or None
    if cafile or capath:
        try:
            return ssl.create_default_context(cafile=cafile, capath=capath)
        except (OSError, ssl.SSLError) as error:
            logger.warning(
                f"Ignoring unusable certificate override (SSL_CERT_FILE={cafile!r}, SSL_CERT_DIR={capath!r}): {error}"
            )

    if sys.platform in _OS_STORE_PLATFORMS:
        try:
            import truststore
        except ImportError:
            pass
        else:
            return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def aiohttp_connector() -> aiohttp.TCPConnector:
    return aiohttp.TCPConnector(ssl=client_ssl_context())


def is_certificate_error(error: BaseException) -> bool:
    return (
        isinstance(error, (aiohttp.ClientConnectorCertificateError, ssl.SSLCertVerificationError))
        or "CERTIFICATE_VERIFY_FAILED" in str(error)
    )


def certificate_failure_message(host: str) -> str:
    message = (
        f"Could not verify the secure connection to {host}: this computer does not trust "
        f"the site's certificate. Usually the system certificate store is missing, or a "
        f"proxy or antivirus inspects HTTPS traffic. Install its root certificate, or set "
        f"SSL_CERT_FILE to a certificate bundle file, then restart PotionUI."
    )
    if sys.platform.startswith("linux"):
        message += " On Linux or WSL, run: sudo apt install ca-certificates && sudo update-ca-certificates."
    return message
