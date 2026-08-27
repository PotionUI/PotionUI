"""
Authentication operations coordinator.
"""
import logging
from datetime import timedelta
from typing import Optional, Tuple, TYPE_CHECKING


from src.platform.security.config import AuthConfig
from src.platform.security.password import PasswordHasher
from src.platform.security.token import TokenCodec
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import HookContext
from src.platform.security.hooks import AUTH_HOOKS
from src.platform.security.claim_store import InstanceClaimStore
from src.platform.security.claim_token import ClaimTokenStore
from src.platform.security.store import UserStore
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.platform.settings.settings import Settings

logger = logging.getLogger(__name__)


class Auth:
    """
    Coordinates authentication operations.

    Combines password hashing, token management, user repository access,
    and plugin hook execution into cohesive auth workflows.
    """

    def __init__(
        self,
        user_repository: UserStore,
        password_hasher: PasswordHasher,
        token_codec: TokenCodec,
        auth_config: AuthConfig,
        plugin_registry: PluginRegistry,
        instance_claim: InstanceClaimStore,
        claim_tokens: ClaimTokenStore,
        settings: "Settings",
    ):
        self.users = user_repository
        self.passwords = password_hasher
        self.tokens = token_codec
        self.config = auth_config
        self.plugins = plugin_registry
        self.instance_claim = instance_claim
        self.claim_tokens = claim_tokens
        self.settings = settings

    def _execute_hook(self, hook: str, data: dict) -> Tuple[dict, bool]:
        """
        Execute a hook and return the context data and whether it was blocked.

        Args:
            hook: The hook definition to execute
            data: Context data for the hook

        Returns:
            Tuple of (context_data, blocked)
        """
        context, results = self.plugins.execute_hook(
            hook,
            initial_data=data
        )

        # Check if any plugin blocked the operation
        blocked = context.data.get("blocked", False)

        return context.data, blocked

    def _registration_open(self) -> bool:
        """Whether register() accepts a new account on an already-claimed
        instance. Governed by the ``registration_policy`` setting (default
        ``closed``); irrelevant while the instance is unclaimed."""
        policy = (self.settings.get_setting("registration_policy", "closed") or "closed")
        return policy.strip().lower() == "open"

    def register(
        self,
        username: str,
        email: str,
        password: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        origin_is_loopback: bool = True,
        claim_token: Optional[str] = None,
    ) -> Tuple[User, str]:
        """
        Register a new user.

        Account type and instance ownership are decided atomically at the
        database layer (``UserStore.create_claiming_instance``): the very first
        registration to win the single-row claim sentinel becomes ADMIN, every
        other registration becomes a regular USER. This is race-safe under
        concurrent registration - two callers can never both become admin.

        Gating before the account is created:
        - While the instance is unclaimed, registration is always allowed so an
          owner can be created, but a claim from a non-loopback origin must
          present the one-time setup token.
        - Once the instance is claimed, the ``registration_policy`` setting
          decides whether new accounts are accepted (default: closed).

        Executes hooks:
        - auth.before_register: Can modify/validate registration data or block
        - auth.after_register: Notification of successful registration

        Args:
            username: Desired username
            email: User's email address
            password: Plain text password (will be hashed)
            ip_address: Optional IP address for logging/hooks
            user_agent: Optional user agent for logging/hooks
            origin_is_loopback: True when the request came from the local
                machine; a loopback owner-claim needs no setup token.
            claim_token: One-time setup token, required to claim an unclaimed
                instance from a non-loopback origin.

        Returns:
            Tuple of (user, access_token)

        Raises:
            ValueError: If username/email exists, registration is closed, the
                setup token is missing/invalid, or a plugin blocks registration.
        """
        # Execute before_register hook
        hook_data, blocked = self._execute_hook(
            AUTH_HOOKS.before_register,
            {
                "username": username,
                "email": email,
                "ip_address": ip_address,
                "user_agent": user_agent
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Registration blocked")
            logger.warning(f"Registration blocked by plugin: {reason}")
            raise ValueError(reason)

        # Allow hooks to modify data
        username = hook_data.get("username", username)
        email = hook_data.get("email", email)

        # Check if username already exists
        if self.users.exists_by_username(username):
            raise ValueError("Username already exists")

        # Check if email already exists
        if self.users.exists_by_email(email):
            raise ValueError("Email already exists")

        # Registration-policy / setup-token gate. The authoritative claim
        # decision is still made atomically in create_claiming_instance below;
        # this pre-check produces clear errors and enforces the token before any
        # account is created. A caller that slips through this window during the
        # brief unclaimed period simply becomes a regular USER (it loses the
        # atomic claim), never a second admin.
        if self.instance_claim.is_claimed():
            if not self._registration_open():
                raise ValueError(
                    "This instance already has an owner. Ask them to add you "
                    "in Administration -> Users."
                )
        elif not origin_is_loopback and not self.claim_tokens.verify(claim_token):
            raise ValueError(
                "This instance isn't set up yet. To create the owner account "
                "from a remote address, enter the setup token shown in the "
                "server console."
            )

        # Hash password, then create the user and attempt the claim atomically.
        hashed_password = self.passwords.hash(password)
        user, became_owner = self.users.create_claiming_instance(
            username=username,
            email=email,
            password_hash=hashed_password,
        )

        if became_owner:
            # The instance now has an owner; retire the one-time setup token.
            self.claim_tokens.clear()

        # Create token for immediate login
        access_token = self.tokens.create_access_token(
            data={"sub": user.username, "user_id": user.id}
        )

        # Execute after_register hook
        self._execute_hook(
            AUTH_HOOKS.after_register,
            {
                "user_id": user.id,
                "username": user.username,
                "email": user.email,
                "account_type": user.account_type.value,
                "token": access_token
            }
        )

        logger.info(
            f"User registered successfully: {username} "
            f"(type: {user.account_type.value}, became_owner: {became_owner})"
        )

        return user, access_token

    def authenticate(
        self,
        username: str,
        password: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        remember_me: bool = False
    ) -> Tuple[User, str]:
        """
        Authenticate user credentials.

        Executes hooks:
        - auth.before_login: Can block login attempts (rate limiting, etc.)
        - auth.after_login: Notification of successful login

        Args:
            username: Username to authenticate
            password: Plain text password
            ip_address: Optional IP address for logging/hooks
            user_agent: Optional user agent for logging/hooks

        Returns:
            Tuple of (user, access_token)

        Raises:
            ValueError: If credentials invalid or login blocked
        """
        # Execute before_login hook
        hook_data, blocked = self._execute_hook(
            AUTH_HOOKS.before_login,
            {
                "username": username,
                "ip_address": ip_address,
                "user_agent": user_agent
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Login blocked")
            logger.warning(f"Login blocked by plugin for user {username}: {reason}")
            raise ValueError(reason)

        # Get user by username
        user = self.users.get_by_username(username)
        if not user or not self.passwords.verify(password, user.password_hash):
            logger.warning(f"Failed login attempt for username: {username}")
            raise ValueError("Incorrect username or password")

        # Update last login
        self.users.update_last_login(user.id)

        # Use extended expiry for "remember me"
        expires_delta = None
        if remember_me:
            expires_delta = timedelta(days=self.config.remember_me_token_expire_days)

        # Create token
        access_token = self.tokens.create_access_token(
            data={"sub": user.username, "user_id": user.id},
            expires_delta=expires_delta
        )

        # Execute after_login hook
        self._execute_hook(
            AUTH_HOOKS.after_login,
            {
                "user_id": user.id,
                "username": user.username,
                "ip_address": ip_address,
                "token": access_token
            }
        )

        logger.info(f"User logged in successfully: {username}")

        return user, access_token

    def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
    ) -> User:
        """
        Change a user's own password after verifying their current one.

        Does not invalidate existing JWTs — sessions are stateless tokens
        with no revocation list, so a password change does not log out
        other active sessions.

        Args:
            user_id: ID of the authenticated user
            current_password: Plain-text current password to verify
            new_password: Plain-text new password (already policy-validated by the DTO)

        Returns:
            The updated User

        Raises:
            ValueError: If the user is missing or current_password is wrong
        """
        user = self.users.get_by_id(user_id)
        if not user:
            raise ValueError("User not found")

        if not self.passwords.verify(current_password, user.password_hash):
            raise ValueError("Current password is incorrect")

        new_hash = self.passwords.hash(new_password)
        updated_user = self.users.update_password(user_id, new_hash)
        if not updated_user:
            raise ValueError("Failed to update password")

        logger.info(f"Password changed for user: {user.username}")
        return updated_user

    def get_user_from_token(self, token: str) -> Optional[User]:
        """
        Decode token and fetch user.

        Args:
            token: JWT token string

        Returns:
            User if token is valid and user exists, None otherwise
        """
        token_data = self.tokens.decode_token(token)
        if token_data is None:
            return None

        return self.users.get_by_id(token_data.user_id)

    def authenticate_websocket(
        self,
        token: Optional[str]
    ) -> Tuple[Optional[User], Optional[str]]:
        """
        WebSocket authentication helper.

        Args:
            token: JWT token string, can be None

        Returns:
            Tuple of (user, error_message)
            - On success: (user, None)
            - On failure: (None, error_message)
        """
        if not token:
            return None, "Authentication required"

        token_data = self.tokens.decode_token(token)
        if token_data is None:
            return None, "Invalid token"

        user = self.users.get_by_id(token_data.user_id)
        if user is None:
            return None, "User not found"

        return user, None
