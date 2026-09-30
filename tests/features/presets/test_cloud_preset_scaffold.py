import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.preset_new import CLOUD_MODES, scaffold
from src.features.presets.linter import PresetLinter
from src.plugin_api.cloud import CLOUD_BLOCKS
from src.platform.util.ids import generate_ulid

REPO = Path(__file__).resolve().parents[3]
SHARED_CLOUD = REPO / "content" / "presets" / "_shared" / "cloud"
ALL_MODES = list(CLOUD_MODES)


def build(tmp_path, modes=None):
    target = tmp_path / "Fake" / "std"
    scaffold(target, generate_ulid(), "Fake std", "image", "cloud", modes or ALL_MODES, False, driver="cloud.fake")
    return target


def lint(tmp_path):
    return [issue for issue in PresetLinter([str(tmp_path)]).lint() if "tests.yml" not in issue.message]


def test_a_scaffolded_cloud_preset_for_every_mode_lints_clean(tmp_path):
    build(tmp_path)

    assert lint(tmp_path) == []


@pytest.mark.parametrize("mode", ALL_MODES)
def test_each_mode_declares_the_driver_and_points_its_tabs_at_shared_blocks(tmp_path, mode):
    target = build(tmp_path, [mode])

    manifest = yaml.safe_load((target / "preset.yml").read_text(encoding="utf-8"))
    form = (target / "modes" / mode / "form.yml").read_text(encoding="utf-8")
    pipeline = yaml.safe_load((target / "modes" / mode / "pipeline.yml").read_text(encoding="utf-8"))["pipeline"]

    assert manifest["engine"] == "cloud" and manifest["driver"] == "cloud.fake"
    assert "{{ paths._shared }}/cloud/models/" in form and "{{ paths._shared }}/cloud/tabs/" in form
    names = [pipe["name"] for pipe in pipeline]
    assert names[-2:] == ["cloud_generate", "gallery"]
    assert names.index("dynamic_prompts_renderer") < names.index("seed_generator") < names.index("cloud_generate")
    assert ("media_loader" in names) == (mode in ("edit", "img2video"))


def test_the_pipeline_task_matches_the_model_picker_of_its_mode(tmp_path):
    target = build(tmp_path)

    for mode, cloud_mode in CLOUD_MODES.items():
        pipeline = yaml.safe_load((target / "modes" / mode / "pipeline.yml").read_text(encoding="utf-8"))["pipeline"]
        task = next(p for p in pipeline if p["name"] == "cloud_generate")["configuration"]["task"]
        picker = yaml.safe_load((SHARED_CLOUD / "models" / f"{task}.yml").read_text(encoding="utf-8"))["fields"][0]
        assert task == cloud_mode.task
        assert picker["configuration"]["tasks"] == [task]


def test_every_shared_block_is_listed_and_every_listed_block_exists():
    on_disk = {path.relative_to(SHARED_CLOUD).as_posix() for path in SHARED_CLOUD.rglob("*.yml")}

    assert on_disk == set(CLOUD_BLOCKS)


def test_scaffolds_reference_only_listed_blocks(tmp_path):
    target = build(tmp_path)

    referenced = set()
    for form in target.rglob("form.yml"):
        referenced.update(re.findall(r"paths\._shared \}\}/cloud/([\w/]+\.yml)", form.read_text(encoding="utf-8")))

    assert referenced and referenced <= set(CLOUD_BLOCKS)


def block_fields(path):
    fields = []
    for field in yaml.safe_load(path.read_text(encoding="utf-8"))["fields"]:
        children = field.get("children")
        if isinstance(children, str):
            fragment = SHARED_CLOUD.parent / children.replace("{{ paths._shared }}/", "")
            fields.extend(block_fields(fragment))
        else:
            fields.append(field)
            for child in children or []:
                fields.append(child)
    return fields


def test_every_control_in_the_blocks_is_bound_to_the_model_or_needs_no_binding():
    unbound = {"model", "count", "seed"}
    for path in (SHARED_CLOUD / "tabs").glob("*.yml"):
        for field in block_fields(path):
            if field["type"] == "accordion" or field.get("name") in unbound:
                continue
            assert field.get("capability"), f"{path.name}: {field.get('name')} is not capability-bound"


def test_edit_and_image_to_video_reuse_the_shared_parameter_fragments_in_the_order_users_see():
    def names(tab):
        return [field["name"] for field in block_fields(SHARED_CLOUD / "tabs" / tab) if "name" in field]

    image = names("image.yml")
    assert image == ["count", "seed", "aspect_ratio", "resolution", "quality", "output_format", "background"]
    assert names("image_edit.yml") == ["references"] + image + ["strength"]
    video = names("video.yml")
    assert video == ["count", "seed", "aspect_ratio", "resolution", "duration", "generate_audio"]
    assert names("img2video.yml") == ["first_frame", "last_frame"] + video


def test_a_mode_without_the_cloud_pipe_is_warned(tmp_path):
    target = build(tmp_path, ["txt2img"])
    (target / "modes" / "txt2img" / "pipeline.yml").write_text("pipeline:\n  - name: gallery\n    id: gallery\n    enabled: true\n", encoding="utf-8")

    messages = [issue.message for issue in lint(tmp_path) if issue.level == "warning"]

    assert any("no 'cloud_generate' pipe" in message for message in messages)


def test_a_cloud_pipe_without_a_task_is_warned(tmp_path):
    target = build(tmp_path, ["txt2img"])
    pipeline = target / "modes" / "txt2img" / "pipeline.yml"
    pipeline.write_text(re.sub(r'\n      task: "txt2img"', "", pipeline.read_text(encoding="utf-8")))

    messages = [issue.message for issue in lint(tmp_path) if issue.level == "warning"]

    assert any("no literal 'task'" in message for message in messages)


def test_a_task_the_model_picker_does_not_offer_is_warned(tmp_path):
    target = build(tmp_path, ["txt2img"])
    pipeline = target / "modes" / "txt2img" / "pipeline.yml"
    pipeline.write_text(pipeline.read_text(encoding="utf-8").replace('task: "txt2img"', 'task: "txt2video"'))

    messages = [issue.message for issue in lint(tmp_path) if issue.level == "warning"]

    assert any("'txt2video' is not among the model field's tasks ['txt2img']" in message for message in messages)


def test_a_native_preset_is_not_checked_for_the_cloud_shape(tmp_path):
    target = tmp_path / "Native" / "std"
    (target / "modes" / "txt2img").mkdir(parents=True)
    (target / "preset.yml").write_text(
        f'schema: 1\nid: "{generate_ulid()}"\nname: "N"\ncategory: "image"\nversion: "1.0.0"\nengine: "native"\nmodes:\n  - txt2img\n'
    , encoding="utf-8")
    (target / "modes" / "txt2img" / "pipeline.yml").write_text("pipeline:\n  - name: gallery\n    id: gallery\n    enabled: true\n", encoding="utf-8")
    (target / "modes" / "txt2img" / "form.yml").write_text('name: "custom"\nfields: []\n', encoding="utf-8")

    assert not [issue for issue in lint(tmp_path) if "cloud_generate" in issue.message]


def run_cli(tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "preset_new.py"), "Fake/std", "--root", str(tmp_path), *args],
        capture_output=True, text=True, encoding="utf-8", cwd=REPO,
    )


def test_the_cli_needs_a_driver_for_the_cloud_engine(tmp_path):
    result = run_cli(tmp_path, "--engine", "cloud")

    assert result.returncode != 0 and "--driver" in result.stderr


def test_the_cli_refuses_modes_it_has_no_blocks_for(tmp_path):
    result = run_cli(tmp_path, "--engine", "cloud", "--driver", "cloud.fake", "--modes", "txt2audio")

    assert result.returncode != 0 and "unknown cloud mode" in result.stderr


def test_the_cli_picks_the_category_from_the_modes(tmp_path):
    result = run_cli(tmp_path, "--engine", "cloud", "--driver", "cloud.fake", "--modes", "txt2video,img2video")

    assert result.returncode == 0, result.stderr
    assert yaml.safe_load((tmp_path / "Fake" / "std" / "preset.yml").read_text(encoding="utf-8"))["category"] == "video"
    assert lint(tmp_path) == []


def test_a_driver_without_the_cloud_engine_is_refused(tmp_path):
    result = run_cli(tmp_path, "--driver", "cloud.fake")

    assert result.returncode != 0
