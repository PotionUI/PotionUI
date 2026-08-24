#!/usr/bin/env python3
"""Generate and rotate the credential encryption key.

    python scripts/rotate_secret_key.py --generate
    python scripts/rotate_secret_key.py --status
    python scripts/rotate_secret_key.py --rotate [--key-file PATH] [--dry-run]

`--rotate` mints a new key, re-encrypts every stored credential under it, and
writes the new key file with the old key retained as a decrypt-only fallback.
Nothing is written until every value has been decrypted successfully: a keyring
that cannot read one credential must not half-rewrite the rest.

The application must not be running during a rotation - it holds its keyring in
memory and would keep writing under the old key.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.backends.repository import BackendRepository  # noqa: E402
from src.features.llm.repository import LLMConfigurationRepository  # noqa: E402
from src.features.plugins.repository import PluginRepository  # noqa: E402
from src.platform.security.secrets import (  # noqa: E402
    SecretCipher,
    SecretDecryptionError,
    SecretKeyError,
    default_key_path,
    generate_key,
    resolve_secret_keys,
    write_key_file,
)


def _collect(plugin_repo, backend_repo, llm_repo):
    """Every stored envelope, as (kind, addressing info, context, ciphertext)."""
    items = []
    for row in plugin_repo.iter_encrypted_settings():
        items.append((
            'setting',
            row['id'],
            f"plugin_settings:{row['plugin_id']}/{row['setting_key']}",
            row['setting_value'],
        ))
    for row in backend_repo.iter_encrypted_configs():
        try:
            config = json.loads(row['config']) if row['config'] else {}
        except (TypeError, ValueError):
            continue
        for key, value in config.items():
            if SecretCipher.is_encrypted(value):
                items.append(('backend', (row['id'], key), f"backends:{row['id']}/{key}", value))
    for row in llm_repo.iter_encrypted_api_keys():
        items.append((
            'llm', row['id'], f"llm_configurations:{row['id']}/api_key", row['api_key'],
        ))
    return items


def cmd_status(plugin_repo, backend_repo, llm_repo) -> int:
    keys = resolve_secret_keys(allow_generate=False)
    cipher = SecretCipher(keys)
    items = _collect(plugin_repo, backend_repo, llm_repo)
    bad = [context for _, _, context, value in items if not cipher.can_decrypt(value)]
    print(f"Keyring: {len(keys)} key(s); key file: {default_key_path()}")
    print(f"Encrypted values: {len(items)}")
    if bad:
        print(f"UNREADABLE with the current keyring ({len(bad)}):")
        for context in sorted(bad):
            print(f"  {context}")
        return 1
    print("All stored credentials decrypt with the current keyring.")
    return 0


def cmd_rotate(plugin_repo, backend_repo, llm_repo, key_file: Path, dry_run: bool) -> int:
    old_keys = resolve_secret_keys(allow_generate=False)
    new_key = generate_key()
    reader = SecretCipher(old_keys)
    writer = SecretCipher([new_key])

    items = _collect(plugin_repo, backend_repo, llm_repo)

    # Decrypt everything first. A single failure aborts before any write, so a
    # partially-readable store is never turned into a partially-rewritten one.
    plaintexts = {}
    failures = []
    for kind, address, context, value in items:
        try:
            plaintexts[context] = reader.decrypt(value, context=context)
        except SecretDecryptionError as exc:
            failures.append(str(exc))

    if failures:
        print("Aborting: some stored credentials cannot be read with the current keyring.")
        for message in failures:
            print(f"  {message}")
        print("\nNothing was written. Restore the missing key, or delete the affected")
        print("credentials in Admin -> Plugins, then rotate again.")
        return 1

    print(f"{len(items)} credential(s) decrypt cleanly.")
    if dry_run:
        print("--dry-run: no changes written.")
        return 0

    backend_configs = {}
    for kind, address, context, _ in items:
        reencrypted = writer.encrypt(plaintexts[context])
        if kind == 'setting':
            plugin_repo.replace_encrypted_value(address, reencrypted)
        elif kind == 'llm':
            llm_repo.replace_api_key(address, reencrypted)
        else:
            backend_id, field = address
            backend_configs.setdefault(backend_id, {})[field] = reencrypted

    for backend_id, fields in backend_configs.items():
        row = next(
            r for r in backend_repo.iter_encrypted_configs() if r['id'] == backend_id
        )
        config = json.loads(row['config']) if row['config'] else {}
        config.update(fields)
        backend_repo.replace_config(backend_id, json.dumps(config))

    # The old key stays in the file as a decrypt-only fallback, so a credential
    # written by a process that had not reloaded yet is still readable.
    write_key_file(key_file, [new_key] + list(old_keys))
    print(f"Rotated {len(items)} credential(s). New key written to {key_file}")
    print("The previous key is retained in that file for decryption only.")
    print("Remove it once you are satisfied, and update POTIONUI_SECRET_KEY if you set it.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--generate', action='store_true', help="Print a fresh key and exit")
    group.add_argument('--status', action='store_true', help="Report what the current keyring can read")
    group.add_argument('--rotate', action='store_true', help="Re-encrypt everything under a new key")
    parser.add_argument('--key-file', type=Path, default=None, help="Where to write the rotated key file")
    parser.add_argument('--dry-run', action='store_true', help="With --rotate: verify only, write nothing")
    args = parser.parse_args()

    if args.generate:
        print(generate_key().decode('ascii'))
        return 0

    plugin_repo = PluginRepository()
    backend_repo = BackendRepository()
    llm_repo = LLMConfigurationRepository()

    try:
        if args.status:
            return cmd_status(plugin_repo, backend_repo, llm_repo)

        return cmd_rotate(
            plugin_repo, backend_repo, llm_repo,
            args.key_file or default_key_path(), args.dry_run,
        )
    except SecretKeyError as exc:
        print(f"Encryption key problem: {exc}", file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
