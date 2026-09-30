import hashlib
import hmac
from typing import Optional

from src.platform.security.secrets import SecretCipher, get_secret_cipher

CLOUD_USER_REF_PURPOSE = "cloud-user-ref"
USER_REF_LENGTH = 32


def cloud_user_ref(backend_id: str, user_id: str, cipher: Optional[SecretCipher] = None) -> str:
    subkey = (cipher or get_secret_cipher()).derive_subkey(CLOUD_USER_REF_PURPOSE)
    digest = hmac.new(subkey, f"{backend_id}:{user_id}".encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:USER_REF_LENGTH]
