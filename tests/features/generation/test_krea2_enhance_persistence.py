"""With the Krea-2 inline enhance pass ON, exactly ONE image is saved and it
survives a reload - the enhanced image, not the base one.

There is a single `gallery` pipe in the pipeline (see
content/presets/marketplace/Krea2/modes/txt2img/pipeline.yml): its `image` input is a
Jinja switch that reads from `compare_enhance` (the enhanced pass) when
Enhance is ON, and from the base `generator` when it's OFF - never both.
Two things have to hold together for the saved file to be usable:

1. Exactly one `gallery` pipe runs per generation, so exactly N files per
   seed reach the filesystem and the `files` table - not 2N. The pipe never
   flags its output `is_derived`; that flag exists to make one saved file
   lead over another file in the *same* saved set, and there is only ever
   one file in the set.
2. `param_emitter` covers every saved index with a single pass. A file's
   index is its position in the generation's file list; an index with no
   parameter row resolves to an empty `parameters` dict in history and
   leaves the export endpoint guessing.

Both are driven through the real pipes, the real output handlers and the real
repositories against a scratch database, then read back from a fresh query -
"survives a reload" means read back from storage, not from anything the run
still holds in memory.
"""
import importlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from tests.fixtures.persistence_base import PersistenceTestBase

from src.features.generation.handlers.gallery_handler import GalleryGenerationOutputHandler
from src.features.generation.handlers.param_handler import ParamGenerationOutputHandler
from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.repository import generation_repo
from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import GalleryGenerationOutput, ParamGenerationOutput
from src.pipelines.pipes.gallery.main import GalleryPipe
from src.pipelines.pipes.param_emitter.main import ParamEmitterPipe

QUANTITY = 2
SEEDS = [1111, 2222]


class TestKrea2InlineEnhancePersistence(PersistenceTestBase):
    def setUp(self):
        super().setUp()

        # Repositories bind `db` at import time (see persistence_base's own
        # comment on this) - `_create_test_database` already redirects
        # repository.py/file_repository.py; the read side used by get_params
        # (parameter/model/source repos) needs the same redirection here.
        for module_path in (
            "src.features.generation.parameter_repository",
            "src.features.generation.model_repository",
            "src.features.generation.source_repository",
        ):
            module = sys.modules.get(module_path) or importlib.import_module(module_path)
            module.db = self.db

        self.storage_dir = tempfile.mkdtemp()
        self.settings = Mock()
        self.settings.get_file_storage_directory.return_value = self.storage_dir

        self.user_id = self.create_test_user()
        self.generation_id = "gen_krea2_enhance"
        # create_test_generation's own form_data serialization (a naive
        # quote-swap, not json.dumps) breaks on booleans - write the row
        # directly with a real JSON payload instead.
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO generations (id, preset_id, form_data, user_id) VALUES (?, ?, ?, ?)",
                (
                    self.generation_id,
                    "4TK1KBQZ2XMB8ME0PTMXS1YJQP",
                    json.dumps({"enhance_enabled": True, "quantity": QUANTITY}),
                    self.user_id,
                ),
            )
        self.query = GenerationHistoryQuery(generation_repo=generation_repo)

    def tearDown(self):
        shutil.rmtree(self.storage_dir, ignore_errors=True)
        super().tearDown()

    def _run_gallery_pipe(self, size, derived=False):
        """One `gallery` node's whole trip: pipe -> GalleryGenerationOutput ->
        handler -> filesystem + files table."""
        images = [Image.new("RGB", size, color=(i * 40, 0, 0)) for i in range(QUANTITY)]
        emitted = []
        GalleryPipe(config={"save": "true", "derived": derived}).process(
            PipeInput(input={"image": images, "seed": list(SEEDS)}), emitted.append
        )

        for output in emitted:
            if isinstance(output, GalleryGenerationOutput):
                GalleryGenerationOutputHandler(
                    self.generation_id, self.user_id, self.settings
                ).handle(output)

    def _run_param_emitter(self, passes=1):
        emitted = []
        ParamEmitterPipe(config={
            "quantity": QUANTITY,
            "passes": passes,
            "parameters": [
                ["positive_prompt", ["a cat", "a dog"]],
                ["negative_prompt", ["", ""]],
                ["cfg", 1.0],
                ["steps", 8],
                ["sampler", "euler"],
                ["resolution", "2048x2048"],
                ["enhance", True],
                ["enhance_detail", "balanced"],
                ["upscale_by", "2x"],
            ],
        }).process(PipeInput(input={"seed": list(SEEDS)}), emitted.append)

        for output in emitted:
            if isinstance(output, ParamGenerationOutput):
                ParamGenerationOutputHandler(
                    self.generation_id, self.user_id, self.settings
                ).handle(output)

    def _persisted_files(self):
        """Read the generation's files back the way history does, from the
        database rather than from anything the run still holds."""
        from src.features.generation.file_repository import file_repo
        return file_repo.get_generation_files(self.generation_id)

    def test_enhance_on_saves_only_the_enhanced_image(self):
        # The pipeline's single `gallery` node reads its `image` input from
        # `compare_enhance` (the enhanced pass) when Enhance is ON, so this
        # is the only image array that ever reaches GalleryPipe for the run.
        self._run_gallery_pipe((64, 64))

        files = self._persisted_files()
        assert len(files) == QUANTITY, (
            f"expected only the enhanced batch to be saved, got {[f.file_path for f in files]}"
        )
        assert all(f.is_derived is False for f in files), (
            "with one file saved per generation there is nothing for it to be "
            "derived from within the saved set"
        )
        assert [f.width for f in files] == [64] * QUANTITY

        paths = [f.file_path for f in files]
        assert len(set(paths)) == len(paths)
        for file_record in files:
            full_path = Path(self.storage_dir) / file_record.file_path
            assert full_path.exists(), f"missing file on disk: {full_path}"

    def test_every_saved_index_resolves_a_full_parameter_row(self):
        self._run_gallery_pipe((64, 64))
        self._run_param_emitter(passes=1)

        files = self._persisted_files()
        assert len(files) == QUANTITY
        expected_keys = {
            "positive_prompt", "negative_prompt", "cfg", "steps", "sampler",
            "resolution", "enhance", "enhance_detail", "upscale_by", "seed",
        }

        for index in range(len(files)):
            params = self.query.get_params(self.generation_id, index, self.user_id)["parameters"]
            missing = expected_keys - set(params)
            assert not missing, f"index {index} missing parameter(s): {missing}"
            assert params["enhance"] is True
            assert params["steps"] == 8
            assert params["resolution"] == "2048x2048"

        for index in range(len(files)):
            params = self.query.get_params(self.generation_id, index, self.user_id)["parameters"]
            assert params["seed"] == SEEDS[index % QUANTITY]
            assert params["positive_prompt"] == ["a cat", "a dog"][index % QUANTITY]

        # Nothing is saved past the last index, so nothing there should have params.
        orphan = self.query.get_params(self.generation_id, len(files), self.user_id)
        assert orphan["parameters"] == {}

    def test_enhance_off_saves_one_batch_with_one_pass_of_params(self):
        self._run_gallery_pipe((32, 32))
        self._run_param_emitter(passes=1)

        files = self._persisted_files()
        assert len(files) == QUANTITY
        assert all(f.is_derived is False for f in files)

        for index in range(QUANTITY):
            params = self.query.get_params(self.generation_id, index, self.user_id)["parameters"]
            assert params["seed"] == SEEDS[index]

        assert self.query.get_params(
            self.generation_id, QUANTITY, self.user_id
        )["parameters"] == {}
