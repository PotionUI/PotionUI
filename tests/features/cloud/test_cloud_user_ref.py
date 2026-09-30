import re

from src.platform.security.secrets import SecretCipher, generate_key
from src.platform.security.user_ref import cloud_user_ref
from src.features.cloud.testing.fake import FakeBehaviour
from tests.features.cloud.cloud_generation_harness import USER_ID, CloudGeneration


def add_user(db):
    with db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
            (USER_ID, USER_ID, f"{USER_ID}@example.test"),
        )


def cipher():
    return SecretCipher([generate_key()])


def test_a_ref_is_stable_for_one_user_and_backend():
    key = cipher()

    assert cloud_user_ref("b1", "u1", key) == cloud_user_ref("b1", "u1", key)


def test_a_ref_differs_by_backend_and_by_user():
    key = cipher()

    refs = {cloud_user_ref("b1", "u1", key), cloud_user_ref("b2", "u1", key), cloud_user_ref("b1", "u2", key)}

    assert len(refs) == 3


def test_a_ref_is_opaque_hex_that_does_not_contain_the_user_id():
    ref = cloud_user_ref("b1", "01HUSERIDENTIFIER", cipher())

    assert re.fullmatch(r"[0-9a-f]{32}", ref) and "01HUSER" not in ref


def test_two_installs_give_the_same_user_different_refs():
    assert cloud_user_ref("b1", "u1", cipher()) != cloud_user_ref("b1", "u1", cipher())


def test_the_subkey_is_derived_per_purpose_and_is_never_the_raw_key():
    raw = generate_key()
    key = SecretCipher([raw])

    assert key.derive_subkey("cloud-user-ref") != raw
    assert key.derive_subkey("cloud-user-ref") != key.derive_subkey("something-else")
    assert key.derive_subkey("cloud-user-ref") == SecretCipher([raw]).derive_subkey("cloud-user-ref")


async def run_twice(mock_db, tmp_path):
    add_user(mock_db)
    run = await CloudGeneration(tmp_path, FakeBehaviour(mode="sync")).start()
    await run.run()
    run.collected.done.clear()
    await run.submit("gen-2")
    await run.finished()
    return run


async def test_the_ref_reaches_the_pipe_config_and_the_provider_request(mock_db, tmp_path):
    run = await run_twice(mock_db, tmp_path)

    expected = cloud_user_ref("cloud-1", USER_ID)
    cloud_config = [
        next(pipe for pipe in prepared if pipe["name"] == "cloud_generate")["config"]["cloud"]
        for prepared in run.prepared
    ]
    assert [config["user_ref"] for config in cloud_config] == [expected, expected]
    assert [request.user_ref for request in run.behaviour.requests] == [expected, expected]


async def test_the_raw_user_id_never_reaches_the_pipe_config_or_the_provider(mock_db, tmp_path):
    run = await run_twice(mock_db, tmp_path)

    assert USER_ID not in repr(run.prepared)
    assert all(USER_ID not in repr(request) for request in run.behaviour.requests)
