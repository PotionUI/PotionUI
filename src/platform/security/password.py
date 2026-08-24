"""
Password hashing utilities using bcrypt.
"""
import bcrypt

# bcrypt only consumes the first 72 bytes of a password; anything beyond that is
# ignored by the algorithm itself. Modern bcrypt raises instead of truncating,
# so we truncate explicitly to keep long passwords working.
_MAX_PASSWORD_BYTES = 72


class PasswordHasher:
    """Handles password hashing and verification using bcrypt."""

    ROUNDS = 12

    def hash(self, password: str) -> str:
        """Hash a plaintext password."""
        salt = bcrypt.gensalt(self.ROUNDS, prefix=b"2b")
        return bcrypt.hashpw(self._encode(password), salt).decode()

    def verify(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash."""
        try:
            return bcrypt.checkpw(self._encode(plain_password), hashed_password.encode())
        except ValueError:
            # Malformed or non-bcrypt hash — treat as a failed login, not a crash.
            return False

    @staticmethod
    def _encode(password: str) -> bytes:
        return password.encode("utf-8")[:_MAX_PASSWORD_BYTES]
