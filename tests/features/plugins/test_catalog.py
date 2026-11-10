import json

import pytest

from src.features.plugins.catalog import CatalogError, core_mismatch, is_installable_plugin_id, parse_catalog

SHA = "a" * 64


def _entry(**overrides):
    entry = {
        "id": "video-producer",
        "name": "Video Producer",
        "version": "0.1.0",
        "description": "Storyboards",
        "license": "GPL-3.0",
        "source": {"repository": "owner/plugins", "path": "video-producer"},
        "requires": {"core": "0.1.0", "plugin_api": None},
        "release": {"archive": "https://example.com/video-producer-0.1.0.zip", "sha256": SHA},
    }
    entry.update(overrides)
    return entry


def _catalog(*entries, schema=1) -> bytes:
    return json.dumps({"schema": schema, "plugins": list(entries)}).encode()


def test_a_catalog_entry_gives_its_id_version_core_requirement_and_release():
    [entry], problems = parse_catalog(_catalog(_entry()))

    assert problems == []
    assert (entry.id, entry.name, entry.version, entry.license) == ("video-producer", "Video Producer", "0.1.0", "GPL-3.0")
    assert entry.requires_core == "0.1.0"
    assert (entry.archive, entry.sha256) == ("https://example.com/video-producer-0.1.0.zip", SHA)


def test_an_entry_without_a_release_is_listed_without_an_archive():
    [entry], problems = parse_catalog(_catalog(_entry(release={"archive": None, "sha256": None})))

    assert problems == []
    assert (entry.archive, entry.sha256) == (None, None)


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"id": "../escape"}, "is not a plugin folder name"),
        ({"id": "Video"}, "is not a plugin folder name"),
        ({"id": "con"}, "is not a plugin folder name"),
        ({"id": "nul.plugin"}, "is not a plugin folder name"),
        ({"id": "trailing."}, "is not a plugin folder name"),
        ({"version": ""}, "version is not a non-empty string"),
        ({"requires": "0.1.0"}, "requires is not an object"),
        ({"requires": {"plugin_api": None}}, "core is not a non-empty string"),
        ({"release": {"archive": "https://example.com/x.zip", "sha256": "A" * 64}}, "sha256 is not 64 lowercase hex"),
        ({"release": {"archive": "https://example.com/x.zip", "sha256": None}}, "sha256 is not 64 lowercase hex"),
        ({"release": {"archive": None, "sha256": SHA}}, "archive is not a URL"),
    ],
)
def test_a_malformed_entry_is_set_aside_with_its_reason_and_the_others_stay(overrides, reason):
    entries, [problem] = parse_catalog(_catalog(_entry(**overrides), _entry(id="preset-editor")))

    assert [entry.id for entry in entries] == ["preset-editor"]
    assert problem.index == 0
    assert reason in problem.reason


def test_an_id_listed_twice_is_set_aside_both_times():
    entries, problems = parse_catalog(_catalog(_entry(), _entry(version="0.2.0"), _entry(id="preset-editor")))

    assert [entry.id for entry in entries] == ["preset-editor"]
    assert [(problem.index, problem.plugin_id) for problem in problems] == [(0, "video-producer"), (1, "video-producer")]


@pytest.mark.parametrize(
    "data, message",
    [
        (b"not json", "not JSON"),
        (b"\xff\xfe", "not JSON"),
        (b"[]", "plugins list"),
        (json.dumps({"schema": 2, "plugins": []}).encode(), "schema 2 is not 1"),
    ],
)
def test_a_document_that_is_not_a_schema_1_catalog_is_refused(data, message):
    with pytest.raises(CatalogError, match=message):
        parse_catalog(data)


@pytest.mark.parametrize(
    "requires_core, core_version, satisfied",
    [
        ("0.1.0", "0.1.0", True),
        ("0.1.0", "0.2.3", True),
        ("0.2.0", "0.1.0", False),
        (">=0.1,<0.2", "0.1.5", True),
        (">=0.1,<0.2", "0.2.0", False),
        ("~=0.1.0", "0.1.9", True),
    ],
)
def test_the_core_requirement_is_a_minimum_version_or_a_range(requires_core, core_version, satisfied):
    assert (core_mismatch(requires_core, core_version) is None) is satisfied


def test_a_core_requirement_that_is_no_version_is_reported():
    reason = core_mismatch("02cce6fddf7ece92a99c7677f71ed1b9a592d16d", "0.1.0")

    assert "is not a version or a version range" in reason


@pytest.mark.parametrize("value", ["video-producer", "a", "a.b_c-1", "x" * 64])
def test_plain_lowercase_ids_are_installable(value):
    assert is_installable_plugin_id(value)


@pytest.mark.parametrize("value", ["", ".hidden", "a/b", "a\\b", "C:", "x" * 65, "aux", "lpt1.txt", "com¹", "end."])
def test_ids_that_are_no_safe_folder_name_are_not_installable(value):
    assert not is_installable_plugin_id(value)
