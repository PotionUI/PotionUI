"""collect_input_assets: rewriting real storage paths into asset:// tokens."""

import json

import pytest

from src.features.generation.input_assets import collect_input_assets
from src.platform.worker_protocol import ProcessedPipeV1


def _pipe(pipe_id, config, inputs=None):
    return ProcessedPipeV1(pipe_id=pipe_id, pipe_type="media_loader", config=config, inputs=inputs or {})


@pytest.fixture
def storage_dir(tmp_path):
    root = tmp_path / "storage"
    (root / "uploads").mkdir(parents=True)
    (root / "generations" / "2026-08-15" / "gen-1").mkdir(parents=True)
    return root


def _plant(storage_dir, relative, content=b"fake bytes"):
    path = storage_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class TestAbsoluteAndRelativeDetection:
    def test_absolute_path_under_storage_becomes_a_token(self, storage_dir):
        planted = _plant(storage_dir, "uploads/x.png")
        pipes = [_pipe("loader", {"media": [{"type": "image", "path": str(planted)}]})]

        rewritten, manifest, _sources = collect_input_assets(pipes, storage_dir)

        assert manifest is not None
        assert len(manifest.assets) == 1
        token = rewritten[0].config["media"][0]["path"]
        assert token == f"asset://{manifest.assets[0].logical_id}"
        assert manifest.assets[0].size_bytes == len(b"fake bytes")

    def test_storage_relative_path_becomes_a_token(self, storage_dir):
        _plant(storage_dir, "generations/2026-08-15/gen-1/1.mp4", b"video bytes")
        pipes = [_pipe("loader", {"media": [{"type": "video", "path": "generations/2026-08-15/gen-1/1.mp4"}]})]

        rewritten, manifest, _sources = collect_input_assets(pipes, storage_dir)

        assert manifest is not None
        assert len(manifest.assets) == 1
        assert rewritten[0].config["media"][0]["path"].startswith("asset://")

    def test_dict_shaped_media_ref_tokenizes_both_path_keys(self, storage_dir):
        """The real shape MediaLoaderField.svelte submits: `path` and
        `relative_path` both name the same file - both must resolve to the
        same token."""
        _plant(storage_dir, "uploads/x.png")
        pipes = [_pipe("loader", {"media": [{
            "type": "image",
            "path": {
                "path": "uploads/x.png",
                "relative_path": "uploads/x.png",
                "url": "/api/media/uploads/x.png",
                "name": "x.png",
                "type": "image",
            },
        }]})]

        rewritten, manifest, _sources = collect_input_assets(pipes, storage_dir)

        assert len(manifest.assets) == 1
        ref = rewritten[0].config["media"][0]["path"]
        assert ref["path"] == ref["relative_path"] == f"asset://{manifest.assets[0].logical_id}"
        assert ref["url"] == "/api/media/uploads/x.png"


class TestDeduplication:
    def test_the_same_file_referenced_by_two_pipes_is_one_manifest_entry(self, storage_dir):
        _plant(storage_dir, "uploads/x.png")
        pipes = [
            _pipe("media_loader", {"media": [{"type": "image", "path": "uploads/x.png"}]}),
            _pipe("param_emitter", {"parameters": [["image", "uploads/x.png"]]}),
        ]

        rewritten, manifest, _sources = collect_input_assets(pipes, storage_dir)

        assert len(manifest.assets) == 1
        loader_token = rewritten[0].config["media"][0]["path"]
        emitter_token = rewritten[1].config["parameters"][0][1]
        assert loader_token == emitter_token == f"asset://{manifest.assets[0].logical_id}"


class TestOriginStripping:
    def test_origin_sibling_keys_are_dropped(self, storage_dir):
        _plant(storage_dir, "uploads/x.png")
        pipes = [_pipe("loader", {
            "image": "uploads/x.png",
            "image__origin": {"generation_id": "gen-1", "file_index": 0},
        })]

        rewritten, _manifest, _sources = collect_input_assets(pipes, storage_dir)

        assert "image__origin" not in rewritten[0].config
        assert rewritten[0].config["image"].startswith("asset://")


class TestUntouchedValues:
    def test_a_string_that_does_not_exist_under_storage_is_untouched(self, storage_dir):
        pipes = [_pipe("loader", {"media": [{"type": "image", "path": "uploads/missing.png"}]})]

        rewritten, manifest, _sources = collect_input_assets(pipes, storage_dir)

        assert manifest is None
        assert rewritten[0].config["media"][0]["path"] == "uploads/missing.png"

    def test_a_file_outside_storage_dir_is_untouched(self, storage_dir, tmp_path):
        outside = tmp_path / "elsewhere.png"
        outside.write_bytes(b"outside bytes")
        pipes = [_pipe("loader", {"media": [{"type": "image", "path": str(outside)}]})]

        rewritten, manifest, _sources = collect_input_assets(pipes, storage_dir)

        assert manifest is None
        assert rewritten[0].config["media"][0]["path"] == str(outside)

    def test_an_ordinary_non_path_string_is_untouched(self, storage_dir):
        pipes = [_pipe("loader", {"sampler": {"name": "euler", "scheduler": "normal"}})]

        rewritten, manifest, _sources = collect_input_assets(pipes, storage_dir)

        assert manifest is None
        assert rewritten[0].config == {"sampler": {"name": "euler", "scheduler": "normal"}}


class TestDeterminism:
    def test_the_same_input_produces_byte_identical_manifest_bytes(self, tmp_path):
        results = []
        for i in range(2):
            root = tmp_path / f"storage-{i}"
            (root / "uploads").mkdir(parents=True)
            _plant(root, "uploads/x.png")
            pipes = [_pipe("loader", {"media": [{"type": "image", "path": "uploads/x.png"}]})]
            _rewritten, manifest, _sources = collect_input_assets(pipes, root)
            results.append(json.dumps(manifest.model_dump(mode="json"), sort_keys=True))

        assert results[0] == results[1]

    def test_no_assets_found_yields_no_manifest(self, storage_dir):
        pipes = [_pipe("loader", {"steps": 20})]

        rewritten, manifest, _sources = collect_input_assets(pipes, storage_dir)

        assert manifest is None
        assert rewritten[0].config == {"steps": 20}


class TestPromptLengthStringsAreNotPaths:
    def test_a_prompt_longer_than_a_filename_passes_through_untouched(self, storage_dir):
        prompt = ", ".join(f"tag{i}" for i in range(120)) + ",\nsecond line, more tags"
        assert len(prompt) > 255
        pipes = [_pipe("generator", {"prompt": prompt})]

        rewritten, manifest, _sources = collect_input_assets(pipes, storage_dir)

        assert manifest is None
        assert rewritten[0].config["prompt"] == prompt
