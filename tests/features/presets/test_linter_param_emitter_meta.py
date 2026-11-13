from src.features.presets.linter import PresetLinter
from tests.features.presets.test_linter import _write_preset


def _messages(tmp_path, pairs_yaml):
    preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01TESTPARAMEMITTER000000000", ["txt2img"])
    (preset_dir / "modes" / "txt2img" / "pipeline.yml").write_text(
        "pipeline:\n"
        "  - name: param_emitter\n"
        "    id: param_emitter\n"
        "    configuration:\n"
        "      parameters:\n"
        f"{pairs_yaml}"
        "  - name: gallery\n    input:\n      - [image, generator, image]\n"
    )
    return {issue.message for issue in PresetLinter([str(tmp_path)]).lint()}


def test_three_element_pairs_add_no_lint_issue_over_two_element_pairs(tmp_path):
    two = _messages(tmp_path / "two", '        - ["duration", "{{ form.duration }}"]\n')
    three = _messages(
        tmp_path / "three",
        '        - ["duration", "{{ form.duration }}", {"label": "Length", "unit": "s", "type": "number"}]\n'
        '        - ["prompter", "alice"]\n',
    )

    assert three == two
