"""Backup and restore of the state PotionUI itself owns.

Plain functions, no container: the `potionui backup` / `potionui restore`
subcommands and (later) an admin surface call exactly the same code.

Every module here is stdlib-only on purpose. `./potionui` runs under whatever
Python 3.12+ the shim finds on PATH, which is not necessarily the venv, so a
backup must not depend on the installed dependency set.
"""
