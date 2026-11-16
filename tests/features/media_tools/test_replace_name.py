import io
import shutil
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from src.features.library.operations.export import export_zip
from src.features.media.media_types import MediaTypeResolver
from src.features.media.records import Upload
from src.platform.media_tools import MediaTool, ToolSpec
from src.platform.media_tools.ops import register_core_tools
from tests.features.media_tools.support import MediaToolsTestBase, needs_ffmpeg
from tests.platform.media_tools.media_factory import make_image, make_video

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def color_key():
    return {"tool": "color_key", "params": {}}


def shrink(size=8):
    return {"tool": "shrink", "params": {"size": size}}


class NoSuffix(MediaTool):
    def run(self, inputs, params, ctx):
        out = ctx.new_path("", stem="raw")
        shutil.copyfile(inputs[0].path, out)
        return [ctx.probe(out, "image")]


class ReplaceNameTestBase(MediaToolsTestBase):

    def setUp(self):
        super().setUp()
        register_core_tools(self.registry)
        self.registry.register(ToolSpec(id="nosuffix", label="No suffix", accepts=("image",), impl=NoSuffix()))
        self.service.publisher.thumbnail_runner = lambda task: None

    def named_image(self, stored, shown, mode="RGB", color=(0, 177, 64)):
        path = make_image(self.uploads_dir / stored, (40, 30), mode, color)
        return self.named(path, "image", shown, width=40, height=30)

    def named(self, path, media_type, shown, **metadata):
        return self.upload_repo.create(Upload(
            user_id=self.user_id,
            filename=path.name,
            original_filename=shown,
            media_type=media_type,
            mime_type=MediaTypeResolver().get_media_type(path.suffix),
            file_size=path.stat().st_size,
            **metadata,
        ))

    def replaced(self, steps, item, mode="replace", target=None):
        output = {"mode": mode}
        if target is not None:
            output["target_item_id"] = target.id
        response = self.client.post(
            "/api/media-tools/runs",
            json={"steps": steps, "inputs": [{"item_id": item.id}], "output": output},
        )
        self.assertEqual(response.status_code, 202, response.text)
        run = self.wait_run(response.json()["data"]["run_id"])
        self.assertEqual(run.status, "completed", run.error)
        self.assertTrue(run.replaced)
        return run, self.upload_repo.get_by_id((target or item).id, self.user_id)

    def exported(self, item):
        collaborators = SimpleNamespace(upload_repository=self.upload_repo, storage_driver=self.driver)
        archive, _ = export_zip(collaborators, [item.id], self.user_id)
        with archive, zipfile.ZipFile(io.BytesIO(archive.read())) as opened:
            return {name: opened.read(name) for name in opened.namelist()}


class TestReplaceThatChangesTheFormat(ReplaceNameTestBase):

    def test_color_key_replacing_a_jpeg_names_the_item_after_its_png_content(self):
        item = self.named_image("photo.jpg", "photo.jpg")

        run, row = self.replaced([color_key()], item)

        self.assertEqual(row.original_filename, "photo.png")
        self.assertEqual(run.items[0].original_filename, "photo.png")
        self.assertEqual(row.mime_type, "image/png")
        self.assertTrue(row.filename.endswith(".png"))
        entries = self.exported(row)
        self.assertEqual(list(entries), ["photo.png"])
        self.assertTrue(entries["photo.png"].startswith(PNG_SIGNATURE))

    def test_a_transparent_fit_canvas_on_a_bmp_names_the_item_after_its_png_content(self):
        item = self.named_image("scan.bmp", "scan.bmp")

        _, row = self.replaced(
            [{"tool": "fit_canvas", "params": {"width": 40, "height": 40, "fill": "transparent"}}], item
        )

        self.assertEqual(row.original_filename, "scan.png")
        self.assertEqual(row.mime_type, "image/png")
        self.assertTrue(row.has_alpha)

    @needs_ffmpeg
    def test_a_flip_replacing_an_avi_names_the_item_after_its_mp4_content(self):
        path = make_video(self.uploads_dir / "clip.avi", seconds=1.0, size=(64, 48))
        item = self.named(path, "video", "clip.avi", width=64, height=48, duration_seconds=1.0)

        _, row = self.replaced([{"tool": "flip", "params": {"axis": "horizontal"}}], item)

        self.assertEqual(row.original_filename, "clip.mp4")
        self.assertEqual(row.mime_type, "video/mp4")
        self.assertTrue(row.filename.endswith(".mp4"))

    def test_only_the_last_extension_of_a_name_with_several_dots_changes(self):
        item = self.named_image("photo.jpg", "trip.2026.final.jpg")

        _, row = self.replaced([color_key()], item)

        self.assertEqual(row.original_filename, "trip.2026.final.png")

    def test_an_upper_case_extension_is_replaced_by_the_stored_extension(self):
        item = self.named_image("photo.jpg", "IMG_0042.JPG")

        _, row = self.replaced([color_key()], item)

        self.assertEqual(row.original_filename, "IMG_0042.png")

    def test_a_name_without_an_extension_is_kept(self):
        item = self.named_image("photo.jpg", "my photo")

        _, row = self.replaced([color_key()], item)

        self.assertEqual(row.original_filename, "my photo")

    def test_a_name_whose_last_part_is_not_a_media_extension_is_kept(self):
        item = self.named_image("photo.jpg", "portrait v2.final")

        _, row = self.replaced([color_key()], item)

        self.assertEqual(row.original_filename, "portrait v2.final")

    def test_a_name_ending_in_a_separator_is_kept(self):
        item = self.named_image("photo.jpg", "photo.jpg/")

        _, row = self.replaced([color_key()], item)

        self.assertEqual(row.original_filename, "photo.jpg/")

    def test_a_result_without_an_extension_keeps_the_name(self):
        item = self.named_image("photo.jpg", "photo.jpg")

        _, row = self.replaced([{"tool": "nosuffix", "params": {}}], item)

        self.assertEqual(row.original_filename, "photo.jpg")
        self.assertEqual(row.mime_type, "application/octet-stream")

    def test_an_item_without_a_display_name_keeps_none(self):
        item = self.named_image("photo.jpg", None)

        _, row = self.replaced([color_key()], item)

        self.assertIsNone(row.original_filename)
        self.assertTrue(row.filename.endswith(".png"))

    def test_saving_an_editor_copy_in_another_format_renames_that_copy(self):
        source = self.named_image("photo.jpg", "photo.jpg")
        copy = self.named_image("photo-copy.jpg", "photo-edited.jpg")

        _, row = self.replaced([color_key()], source, mode="update", target=copy)

        self.assertEqual(row.original_filename, "photo-edited.png")
        self.assertEqual(self.upload_repo.get_by_id(source.id, self.user_id).original_filename, "photo.jpg")


class TestReplaceThatKeepsTheFormat(ReplaceNameTestBase):

    def test_an_upper_case_jpg_name_is_kept_over_jpeg_content(self):
        item = self.named_image("photo.jpg", "IMG_0042.JPG")

        _, row = self.replaced([shrink()], item)

        self.assertEqual(row.original_filename, "IMG_0042.JPG")
        self.assertTrue(row.filename.endswith(".jpg"))

    def test_a_jpeg_name_is_kept_over_a_jpg_file(self):
        item = self.named_image("photo.jpg", "scan.jpeg")

        _, row = self.replaced([shrink()], item)

        self.assertEqual(row.original_filename, "scan.jpeg")

    def test_a_jpg_name_is_kept_over_a_jpeg_file(self):
        item = self.named_image("photo.jpeg", "scan.jpg")

        _, row = self.replaced([shrink()], item)

        self.assertEqual(row.original_filename, "scan.jpg")
        self.assertTrue(row.filename.endswith(".jpeg"))

    def test_color_key_on_a_png_keeps_the_name(self):
        item = self.named_image("logo.png", "logo.png")

        _, row = self.replaced([color_key()], item)

        self.assertEqual(row.original_filename, "logo.png")

    def test_a_name_an_earlier_replace_left_wrong_is_mended_by_a_replace_that_keeps_the_format(self):
        item = self.named_image("photo.png", "photo.jpg")

        _, row = self.replaced([shrink()], item)

        self.assertEqual(row.original_filename, "photo.png")
        self.assertTrue(row.filename.endswith(".png"))

    def test_a_replace_that_keeps_the_name_does_not_write_it(self):
        item = self.named_image("photo.jpg", "IMG_0042.JPG")

        with patch.object(self.upload_repo, "replace_file", wraps=self.upload_repo.replace_file) as spy:
            _, row = self.replaced([shrink()], item)

        self.assertIsNone(spy.call_args.kwargs["original_filename"])
        self.assertEqual(row.original_filename, "IMG_0042.JPG")

    def test_a_name_that_already_names_the_new_format_is_kept(self):
        item = self.named_image("photo.jpg", "mislabelled.png")

        _, row = self.replaced([color_key()], item)

        self.assertEqual(row.original_filename, "mislabelled.png")
