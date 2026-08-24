#!/usr/bin/env python3
"""Reset a user's password.

Usage:
    ./reset_password <username> [newpassword]

If newpassword is omitted it is read from a hidden prompt, which keeps it out of
shell history and the process table.
"""
import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.platform.security.password import PasswordHasher
from src.features.users.repository import user_repo


def main() -> int:
    args = sys.argv[1:]
    if not 1 <= len(args) <= 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    username = args[0]
    user = user_repo.get_by_username(username)
    if user is None:
        print(f"No user named {username!r}", file=sys.stderr)
        return 1

    if len(args) == 2:
        password = args[1]
    else:
        password = getpass(f"New password for {username}: ")
        if password != getpass("Repeat: "):
            print("Passwords do not match", file=sys.stderr)
            return 1

    if not password:
        print("Password must not be empty", file=sys.stderr)
        return 1

    user_repo.update(user.id, password_hash=PasswordHasher().hash(password))
    print(f"Password updated for {username} ({user.id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
