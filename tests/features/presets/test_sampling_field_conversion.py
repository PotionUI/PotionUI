"""Every native preset that picks a sampler does it through the registry-driven
`type: sampler` / `type: schedule` field types, with the two sharing one row.

The assertions read the tab YAML directly (the layout contract is authored
there) and then render each field through its own field type, which is where the
registry is consulted -- a raw YAML assertion alone would pass on a `family:`
value no registry entry answers to, leaving the user an empty dropdown.

Two families are deliberately NOT converted and are pinned here so a later sweep
does not "finish the job" and break them:

- MiniMax-H3 / MiniMax-H3-VDN run their own sampler and scheduler tables
  (`generator/video_minimax_h3/samplers.py` and `schedule.py`) over TWO paired
  sigma grids (video + audio). Their key space overlaps the core registry's only
  by coincidence: `sa_solver` exists there and nowhere else, and their `simple`
  scheduler is not a core schedule key at all. A registry-driven picker would
  hide `sa_solver` and offer keys the pipe rejects outright.
- SDXL is an epsilon/k-diffusion family whose pipe carries its own uppercase
  sampler names (`EULER`, `EULER_A`, ...) and its own scheduler list.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.fields.sampling_fields import SamplerField, ScheduleField
from src.platform.runtime.native.sampling.registry import sampler_registry, schedule_registry

MARKETPLACE = Path("content/presets/marketplace")

# (tab file, family, sampler default, schedule default)
CONVERTED = [
    ("Anima/modes/txt2img", "anima", "euler", "shift"),
    ("Flux1/modes/txt2img", "flux", "euler", "shift"),
    ("Flux2/modes/txt2img", "flux", "euler", "shift"),
    ("Krea2/modes/txt2img", "krea2", "euler", "shift"),
    ("LTX-2/modes/video", "ltx", "euler", "shift"),
    ("LTX-2.5/modes/video", "ltx", "euler_ancestral", "ltx_dynamic"),
    ("QwenImage/modes/txt2img", "qwen_image", "euler", "shift"),
    ("QwenImage/modes/img2img", "qwen_image", "euler", "shift"),
    ("QwenImage/modes/edit", "qwen_image", "euler", "shift"),
    ("Wan/modes/video", "wan", "unipc", "shift"),
    ("ZImage/modes/txt2img", "z_image", "euler", "shift"),
]

UNCONVERTED = [
    "MiniMax-H3/modes/video",
    "MiniMax-H3-VDN/modes/video",
    "SDXL/modes/txt2img",
    "SDXL/modes/inpaint",
]


def _tab(mode_dir: str) -> dict:
    return yaml.safe_load((MARKETPLACE / mode_dir / "tabs/advanced.yml").read_text())


def _walk(node):
    for child in node.get("children") or []:
        yield child
        yield from _walk(child)


def _fields(tab: dict):
    out = {}
    for section in tab["fields"]:
        for node in [section, *_walk(section)]:
            if node.get("name"):
                out[node["name"]] = node
    return out


def _rows(tab: dict):
    for section in tab["fields"]:
        for node in [section, *_walk(section)]:
            if node.get("type") == "row":
                yield node


@pytest.mark.parametrize("mode_dir,family,sampler_default,schedule_default", CONVERTED)
def test_sampler_and_schedule_are_registry_driven(mode_dir, family, sampler_default, schedule_default):
    fields = _fields(_tab(mode_dir))

    sampler = fields["sampler"]
    assert sampler["type"] == "sampler"
    assert sampler["default"] == sampler_default
    assert sampler["configuration"]["family"] == family
    assert "options" not in sampler["configuration"]
    # `lcm` is pinned per pipeline for a distilled/consistency checkpoint and
    # degrades a normal one, so it is never a dropdown row. The LTX presets
    # already narrow with `include`, which cannot admit it.
    if "include" in sampler["configuration"]:
        assert "lcm" not in sampler["configuration"]["include"]
    else:
        assert "lcm" in sampler["configuration"]["exclude"]

    schedule = fields["schedule"]
    assert schedule["type"] == "schedule"
    assert schedule["label"] == "Schedule"
    assert schedule["default"] == schedule_default
    assert schedule["configuration"]["family"] == family
    # `manual` is the pipeline's own escape hatch (the Manual sigmas textbox),
    # never a dropdown row -- it owns the step count and ignores `steps`.
    assert "manual" in schedule["configuration"]["exclude"]
    assert "options" not in schedule["configuration"]


@pytest.mark.parametrize("mode_dir,family,sampler_default,schedule_default", CONVERTED)
def test_rendered_options_are_non_empty_and_contain_the_default(
    mode_dir, family, sampler_default, schedule_default
):
    fields = _fields(_tab(mode_dir))

    rendered = SamplerField(Mock()).output({**fields["sampler"], "validation": {}})
    values = [o["value"] for o in rendered["options"]]
    assert values, f"{mode_dir}: sampler family {family!r} matched no registry entry"
    assert sampler_default in values
    assert "lcm" not in values
    assert sampler_registry.has(sampler_default)

    rendered = ScheduleField(Mock()).output({**fields["schedule"], "validation": {}})
    values = [o["value"] for o in rendered["options"]]
    assert values, f"{mode_dir}: schedule family {family!r} matched no registry entry"
    assert schedule_default in values
    assert "manual" not in values
    assert schedule_registry.has(schedule_default)


@pytest.mark.parametrize("mode_dir,family,sampler_default,schedule_default", CONVERTED)
def test_sampler_and_schedule_share_one_row(mode_dir, family, sampler_default, schedule_default):
    tab = _tab(mode_dir)
    shared = [
        row for row in _rows(tab)
        if {c.get("name") for c in row.get("children") or []} >= {"sampler", "schedule"}
    ]
    assert len(shared) == 1, f"{mode_dir}: Sampler and Schedule must sit in exactly one shared row"


def test_ltx_sampler_is_narrowed_to_single_step_samplers():
    # generator/video_ltx re-derives its conditioned-token clamp and x0 blend at
    # every step, so a multistep integrator that mixes PAST velocities in is
    # invalid there -- see that pipe's module docstring.
    for mode_dir in ("LTX-2/modes/video", "LTX-2.5/modes/video"):
        include = _fields(_tab(mode_dir))["sampler"]["configuration"]["include"]
        assert set(include) == {"euler", "euler_ancestral", "euler_cfg_pp", "euler_ancestral_cfg_pp"}


@pytest.mark.parametrize("mode_dir", UNCONVERTED)
def test_pipe_local_sampler_namespaces_stay_plain_selects(mode_dir):
    """See this module's docstring: these families do not draw from the core
    registries, so a registry-driven picker would offer keys their pipes reject
    and hide keys only they have."""
    fields = _fields(_tab(mode_dir))
    assert fields["sampler"]["type"] == "select"
    assert fields["sampler"]["configuration"]["options"]
