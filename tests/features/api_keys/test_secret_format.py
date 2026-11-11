import re

from src.features.api_keys.operations import secret_format as fmt

SCANNER_PATTERN = re.compile(r"pui_[0-9A-Za-z]{46}")


def test_a_secret_is_fifty_characters_matching_the_scanner_pattern():
    secret = fmt.generate_secret()

    assert len(secret) == 50
    assert SCANNER_PATTERN.fullmatch(secret)


def test_secrets_do_not_repeat():
    assert len({fmt.generate_secret() for _ in range(200)}) == 200


def test_the_checksum_of_a_generated_secret_verifies():
    assert fmt.has_valid_checksum(fmt.generate_secret())


def test_a_changed_checksum_character_fails():
    secret = fmt.generate_secret()
    swapped = "A" if secret[-1] != "A" else "B"

    assert not fmt.has_valid_checksum(secret[:-1] + swapped)


def test_a_changed_body_character_fails():
    secret = fmt.generate_secret()
    swapped = "A" if secret[10] != "A" else "B"

    assert not fmt.has_valid_checksum(secret[:10] + swapped + secret[11:])


def test_the_wrong_length_prefix_or_alphabet_fails():
    secret = fmt.generate_secret()

    assert not fmt.has_valid_checksum(secret[:-1])
    assert not fmt.has_valid_checksum("x" + secret[1:])
    assert not fmt.has_valid_checksum(secret[:10] + "-" + secret[11:])
    assert not fmt.has_valid_checksum(secret[:10] + "é" + secret[11:])


def test_the_hash_is_a_sha256_of_the_whole_string():
    import hashlib

    secret = fmt.generate_secret()

    assert fmt.hash_secret(secret) == hashlib.sha256(secret.encode()).hexdigest()
    assert fmt.hash_secret(secret) != fmt.hash_secret(secret + "x")


def test_the_display_prefix_and_last_four_never_reveal_the_whole_key():
    secret = fmt.generate_secret()

    assert fmt.display_prefix(secret) == secret[:12]
    assert fmt.last_four(secret) == secret[-4:]
    assert len(fmt.display_prefix(secret)) + len(fmt.last_four(secret)) < len(secret) // 2
