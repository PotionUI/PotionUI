"""
Test fixtures for user-related data.

Provides fixtures for creating sample users with different
account types and configurations.
"""

import pytest
from datetime import datetime

from src.platform.security.user import User, AccountType
from src.platform.util.ids import generate_ulid


@pytest.fixture
def sample_user(test_db) -> User:
    """
    Create a sample user record in the test database.

    Creates a standard user account with typical fields populated.
    The password hash is a placeholder and not meant for actual authentication.

    Args:
        test_db: Test database fixture

    Returns:
        User: Sample user instance with USER account type
    """
    user_id = generate_ulid()

    user = User(
        id=user_id,
        username='testuser',
        email='test@example.com',
        password_hash='$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5GyYL3MQzYGJa',  # hashed 'password123'
        account_type=AccountType.USER,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        last_login=None
    )

    # Insert user into database
    with test_db.get_cursor() as cursor:
        cursor.execute("""
            INSERT INTO users (
                id, username, email, password_hash, account_type,
                created_at, updated_at, last_login
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user.id,
            user.username,
            user.email,
            user.password_hash,
            user.account_type.value if isinstance(user.account_type, AccountType) else user.account_type,
            user.created_at.isoformat() if user.created_at else None,
            user.updated_at.isoformat() if user.updated_at else None,
            user.last_login.isoformat() if user.last_login else None
        ))

    return user


@pytest.fixture
def sample_admin_user(test_db) -> User:
    """
    Create a sample admin user record in the test database.

    Creates an admin account with elevated privileges.
    Useful for testing authorization and admin-only features.

    Args:
        test_db: Test database fixture

    Returns:
        User: Sample user instance with ADMIN account type
    """
    user_id = generate_ulid()

    user = User(
        id=user_id,
        username='adminuser',
        email='admin@example.com',
        password_hash='$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5GyYL3MQzYGJa',
        account_type=AccountType.ADMIN,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        last_login=datetime.now()
    )

    # Insert user into database
    with test_db.get_cursor() as cursor:
        cursor.execute("""
            INSERT INTO users (
                id, username, email, password_hash, account_type,
                created_at, updated_at, last_login
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user.id,
            user.username,
            user.email,
            user.password_hash,
            user.account_type.value if isinstance(user.account_type, AccountType) else user.account_type,
            user.created_at.isoformat() if user.created_at else None,
            user.updated_at.isoformat() if user.updated_at else None,
            user.last_login.isoformat() if user.last_login else None
        ))

    return user


@pytest.fixture
def sample_users_batch(test_db) -> list[User]:
    """
    Create multiple sample user records for batch testing.

    Creates 5 different users with varying attributes for testing
    list operations, pagination, and filtering.

    Args:
        test_db: Test database fixture

    Returns:
        list[User]: List of sample user instances
    """
    users = []

    for i in range(5):
        user_id = generate_ulid()
        username = f'testuser{i}'
        email = f'test{i}@example.com'

        user = User(
            id=user_id,
            username=username,
            email=email,
            password_hash='$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5GyYL3MQzYGJa',
            account_type=AccountType.ADMIN if i == 0 else AccountType.USER,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_login=None
        )

        # Insert user into database
        with test_db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO users (
                    id, username, email, password_hash, account_type,
                    created_at, updated_at, last_login
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user.id,
                user.username,
                user.email,
                user.password_hash,
                user.account_type.value if isinstance(user.account_type, AccountType) else user.account_type,
                user.created_at.isoformat() if user.created_at else None,
                user.updated_at.isoformat() if user.updated_at else None,
                user.last_login.isoformat() if user.last_login else None
            ))

        users.append(user)

    return users
