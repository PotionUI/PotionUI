import asyncio
import threading
import unittest
from pathlib import Path

from src.features.media_tools.dto import InputRef, OutputSpec, StepRequest, ToolDTO
from src.features.media_tools.publisher import MediaToolPublisher
from src.features.media.media_types import MediaTypeResolver
from src.platform.filesystem.storage_driver import LocalFileStorageDriver
from src.platform.http.errors import DomainError, InvalidInputError, NotFoundError
from src.platform.media_tools import InvalidToolParams, MediaTool, ToolMedia, ToolSpec
from tests.features.media_tools.support import MediaToolsTestBase, WAIT_SECONDS
from tests.platform.media_tools.media_factory import make_image


class NoLocalPathDriver(LocalFileStorageDriver):

    def local_path(self, key):
        return None


class Picky(MediaTool):

    def image(self, image, params):
        return image

    def check_params(self, params):
        if params["size"] == 13:
            raise InvalidToolParams("size", "Thirteen is unlucky")


class TestServiceApi(MediaToolsTestBase):

    def user(self):
        return self.make_user(self.user_id)

    def test_a_run_hands_its_tool_the_running_user_id(self):
        from unittest.mock import patch
        from tests.features.media_tools.support import Stack

        seen = []
        original = Stack.run

        def recording(tool, inputs, params, ctx):
            seen.append(ctx.user_id)
            return original(tool, inputs, params, ctx)

        first, second = self.image_item(), self.image_item(name="two.png")
        with patch.object(Stack, "run", recording):
            run = self.finish([{"tool": "stack"}], [{"item_id": first.id}, {"item_id": second.id}])

        self.assertEqual(run.status, "completed")
        self.assertEqual(seen, [self.user_id])

    def test_catalog_returns_tool_dtos_with_remembered_params(self):
        item = self.image_item()
        self.finish([{"tool": "shrink", "params": {"size": 9}}], [{"item_id": item.id}])

        tools = self.service.catalog(self.user(), kind="image", scope="library")

        self.assertTrue(all(isinstance(tool, ToolDTO) for tool in tools))
        by_id = {tool.id: tool for tool in tools}
        self.assertEqual(by_id["shrink"].last_params, {"size": 9})
        self.assertIsNone(by_id["grow"].last_params)
        self.assertNotIn("acme.panel", by_id)

    def test_last_params_reads_the_remembered_values_for_a_user(self):
        item = self.image_item()
        self.finish([{"tool": "shrink", "params": {"size": 9}}], [{"item_id": item.id}])

        self.assertEqual(self.service.last_params(self.user(), "shrink"), {"size": 9})
        self.assertIsNone(self.service.last_params(self.user(), "grow"))

    def test_start_run_accepts_plain_dicts_pydantic_models_and_a_bare_user_id(self):
        item = self.image_item()

        by_dict = self.service.start_run(
            self.user_id, [{"tool": "shrink", "params": {"size": 5}}], [{"item_id": item.id}], {"mode": "new"}
        )
        by_model = self.service.start_run(
            self.user(), [StepRequest(tool="shrink")], [InputRef(item_id=item.id)], OutputSpec(mode="new")
        )

        self.assertEqual(self.wait_run(by_dict).status, "completed")
        self.assertEqual(self.wait_run(by_model).status, "completed")

    def test_get_run_and_cancel_run_raise_not_found_for_unknown_and_foreign_runs(self):
        item = self.image_item()
        run_id = self.service.start_run(self.user(), [{"tool": "shrink"}], [{"item_id": item.id}])
        self.wait_run(run_id)

        with self.assertRaises(NotFoundError):
            self.service.get_run(self.make_user(self.other_user_id), run_id)
        with self.assertRaises(NotFoundError):
            self.service.cancel_run(self.make_user(self.other_user_id), run_id)
        with self.assertRaises(NotFoundError):
            self.service.get_run(self.user(), "nope")
        self.assertEqual(self.service.get_run(self.user(), run_id).status, "completed")

    def test_malformed_steps_are_an_invalid_params_error(self):
        item = self.image_item()

        with self.assertRaises(InvalidInputError) as caught:
            self.service.start_run(self.user(), [{"params": {}}], [{"item_id": item.id}])

        self.assertEqual(caught.exception.code, "invalid_params")

    def test_a_cross_field_check_in_the_tool_is_reported_with_its_field_and_step(self):
        self.registry.register(ToolSpec(
            id="picky", label="Picky", accepts=("image",), impl=Picky(),
            params=[{"name": "size", "type": "integer", "label": "Size", "default": 1}],
        ))
        item = self.image_item()

        with self.assertRaises(InvalidInputError) as caught:
            self.service.start_run(
                self.user(),
                [{"tool": "shrink"}, {"tool": "picky", "params": {"size": 13}}],
                [{"item_id": item.id}],
            )

        error = caught.exception
        self.assertEqual(error.code, "invalid_params")
        self.assertEqual(error.extra["field"], "size")
        self.assertEqual(error.extra["step_index"], 1)
        self.assertEqual(error.message, "Thirteen is unlucky")


class TestRunSync(MediaToolsTestBase):

    def user(self):
        return self.make_user(self.user_id)

    def test_run_sync_waits_and_returns_the_items(self):
        item = self.image_item()

        result = asyncio.run(self.service.run_sync(
            self.user(), [{"tool": "shrink", "params": {"size": 7}}], [{"item_id": item.id}]
        ))

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "completed")
        self.assertEqual((result.items[0].width, result.items[0].height), (7, 7))
        self.assertFalse(result.replaced)
        self.assertIsNone(result.error)
        self.assertTrue(result.run_id)

    def test_run_sync_reports_a_failed_run_in_the_result_instead_of_raising(self):
        item = self.image_item()

        result = asyncio.run(self.service.run_sync(self.user(), [{"tool": "explode"}], [{"item_id": item.id}]))

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "tool_failed")
        self.assertEqual(result.items, [])

    def test_run_sync_cancels_the_run_when_the_timeout_passes(self):
        item = self.image_item()

        result = asyncio.run(self.service.run_sync(
            self.user(), [{"tool": "block"}], [{"item_id": item.id}], timeout=0.3
        ))

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.error.code, "timeout")
        self.assertEqual(self.jobs.counts()["running"], 0)
        self.assertEqual(self.scratch_entries(), [])
        self.assertEqual(len(self.upload_rows()), 1)

    def test_run_sync_raises_validation_errors_before_any_run_exists(self):
        item = self.image_item()

        with self.assertRaises(DomainError) as caught:
            asyncio.run(self.service.run_sync(self.user(), [{"tool": "nope"}], [{"item_id": item.id}]))

        self.assertEqual(caught.exception.code, "unknown_tool")
        self.assertEqual(self.jobs.counts(), {"queued": 0, "running": 0, "finished": 0})

    def test_run_sync_cancels_its_run_when_the_awaiting_task_is_cancelled(self):
        item = self.image_item()

        async def scenario():
            task = asyncio.create_task(self.service.run_sync(
                self.user(), [{"tool": "block"}], [{"item_id": item.id}], timeout=60
            ))
            await asyncio.to_thread(self.gate.entered.acquire, True, WAIT_SECONDS)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())

        self.assertEqual(self.jobs.counts()["running"], 0)
        self.assertEqual(self.scratch_entries(), [])
        self.assertEqual(len(self.upload_rows()), 1)

    def test_a_cancel_that_lands_while_the_run_is_still_being_started_does_not_leak_the_run(self):
        item = self.image_item()
        original = self.service.start_run
        submitted = threading.Event()

        def slow_start(*args, **kwargs):
            run_id = original(*args, **kwargs)
            submitted.set()
            threading.Event().wait(0.3)
            return run_id

        self.service.start_run = slow_start

        async def scenario():
            task = asyncio.create_task(self.service.run_sync(
                self.user(), [{"tool": "block"}], [{"item_id": item.id}], timeout=60
            ))
            await asyncio.to_thread(submitted.wait, WAIT_SECONDS)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())

        self.assertEqual(self.jobs.counts()["running"], 0)
        self.assertEqual(self.jobs.counts()["finished"], 1)
        self.assertEqual(self.scratch_entries(), [])
        self.assertEqual(len(self.upload_rows()), 1)

    def test_run_blocking_is_the_synchronous_twin(self):
        item = self.image_item()

        result = self.service.run_blocking(
            self.user(), [{"tool": "shrink"}], [{"item_id": item.id}], {"mode": "replace"}, timeout=WAIT_SECONDS
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.replaced)
        self.assertEqual(result.items[0].id, item.id)


class TestDriverBypass(MediaToolsTestBase):

    def setUp(self):
        super().setUp()
        self.driver = NoLocalPathDriver(str(self.storage_dir))
        self.service = self.make_service(self.driver)
        self.client.close()
        self.client = self.make_client(self.service)

    def test_a_new_result_is_staged_and_published_through_the_driver(self):
        item = self.image_item()

        run = self.finish([{"tool": "shrink"}], [{"item_id": item.id}])

        self.assertEqual(run.status, "completed")
        self.assertTrue(self.driver.exists(f"uploads/{run.items[0].filename}"))

    def test_a_replace_removes_the_old_key_through_the_driver(self):
        item = self.image_item()

        run = self.finish([{"tool": "shrink"}], [{"item_id": item.id}], mode="replace")

        self.assertEqual(run.status, "completed")
        self.assertFalse(self.driver.exists(f"uploads/{item.filename}"))
        self.assertTrue(self.driver.exists(f"uploads/{run.items[0].filename}"))

    def test_a_generation_file_is_read_through_the_driver(self):
        record, _ = self.generation_file()

        run = self.finish([{"tool": "shrink"}], [{"generation_file_id": record.id}])

        self.assertEqual(run.status, "completed")


class FailingRepository:

    def __init__(self, inner, fail_on=2):
        self.inner = inner
        self.calls = 0
        self.fail_on = fail_on

    def create(self, upload):
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("insert failed")
        return self.inner.create(upload)

    def delete(self, filename, user_id):
        return self.inner.delete(filename, user_id)

    def get_by_id(self, upload_id, user_id):
        return self.inner.get_by_id(upload_id, user_id)

    def replace_file(self, **kwargs):
        return None


class TestPublisher(MediaToolsTestBase):

    def publisher(self, repository=None):
        return MediaToolPublisher(
            repository or self.upload_repo, MediaTypeResolver(), self.driver, self.guard
        )

    def outputs(self, count):
        directory = Path(self.temp_dir) / "outputs"
        directory.mkdir(exist_ok=True)
        medias = []
        for index in range(count):
            path = make_image(directory / f"out{index}.png", (6 + index, 6))
            medias.append(ToolMedia(path=path, kind="image", width=6 + index, height=6))
        return medias

    def test_a_row_failure_in_the_middle_rolls_back_rows_and_files_already_published(self):
        publisher = self.publisher(FailingRepository(self.upload_repo, fail_on=2))

        with self.assertRaises(RuntimeError):
            publisher.publish_new(self.user_id, self.outputs(3), ["a.png", "b.png", "c.png"])

        self.assertEqual(self.stored_files(), [])
        self.assertEqual(self.upload_rows(), [])

    def test_publishing_stores_every_output_and_names_the_rows(self):
        rows = self.publisher().publish_new(self.user_id, self.outputs(2), ["a.png", None])

        self.assertEqual([row.original_filename for row in rows], ["a.png", None])
        self.assertEqual(sorted(self.stored_files()), sorted(row.filename for row in rows))
        self.assertEqual([row.media_type for row in rows], ["image", "image"])
        self.assertEqual([row.mime_type for row in rows], ["image/png", "image/png"])
        self.assertEqual([(row.width, row.height) for row in rows], [(6, 6), (7, 6)])

    def test_a_replace_the_row_refuses_to_update_leaves_the_original_and_removes_the_new_file(self):
        item = self.image_item()
        publisher = self.publisher(FailingRepository(self.upload_repo))

        with self.assertRaises(NotFoundError):
            publisher.publish_replace(self.user_id, item, f"uploads/{item.filename}", self.outputs(1)[0])

        self.assertEqual(self.stored_files(), [item.filename])

    def test_a_replace_admits_only_the_size_difference(self):
        item = self.image_item(size=(40, 30))
        output = self.outputs(1)[0]

        updated = self.publisher().publish_replace(self.user_id, item, f"uploads/{item.filename}", output)

        admitted = self.guard.admits[-1]
        self.assertEqual(admitted.incoming_bytes, updated.file_size - item.file_size)
        self.assertEqual(admitted.item_bytes, ())


if __name__ == "__main__":
    unittest.main()
