import urllib.request

import pytest
import yaml

from scripts import recommendations_fill as fill

SHA = "ab" * 32
MASKED = "*" * 64

YAML = """fields:
  - name: "model"
    type: "model"
    configuration:
      model_type: "diffusion_model"
      recommendations:
        - name: "dit-int8.safetensors"
          provider: "huggingface"
          ref: '{"repo": "org/gated", "file": "diffusion_models/dit-int8.safetensors", "revision": "main"}'
          size: "1 GB"
          description: "int8"
        - name: "vae_fp16.safetensors"
          link: https://huggingface.co/org/open/resolve/main/vae/vae_fp16.safetensors
          size: 2 MB
          sha256: %s
        - name: "{{ vars.file }}"
          provider: "huggingface"
          ref: '{"repo": "org/x", "file": "{{ vars.file }}"}'
        - name: "lost.safetensors"
          provider: "huggingface"
          ref: '{"repo": "org/open", "file": "lost.safetensors"}'
""" % SHA

TREES = {
    "https://huggingface.co/api/models/org/gated/tree/main/diffusion_models": [
        {"type": "file", "path": "diffusion_models/dit-int8.safetensors", "size": 135, "lfs": {"oid": MASKED, "size": 3 * 1024 ** 3}},
    ],
    "https://huggingface.co/api/models/org/open/tree/main/vae": [
        {"type": "file", "path": "vae/vae_fp16.safetensors", "size": 2097152, "lfs": {"oid": SHA, "size": 2097152}},
    ],
    "https://huggingface.co/api/models/org/open/tree/main": [],
    "https://huggingface.co/api/models/org/gated": {"gated": "manual"},
    "https://huggingface.co/api/models/org/open": {"gated": False},
}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access in a test")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


def _fetch(calls):
    def fetch(url, headers):
        calls.append((url, dict(headers)))
        return TREES[url]

    return fetch


def test_fill_adds_exact_size_precision_and_gating_without_touching_other_lines():
    calls = []
    text, changes, skipped = fill.fill_text(YAML, fill.HubLookup(fetch=_fetch(calls), token="t0k"))
    data = yaml.safe_load(text)["fields"][0]["configuration"]["recommendations"]

    assert data[0]["size_bytes"] == 3 * 1024 ** 3
    assert data[0]["size"] == "3.00 GB"
    assert data[0]["precision"] == "int8"
    assert data[0]["gated"] is True
    assert data[0]["license_url"] == "https://huggingface.co/org/gated"
    assert "sha256" not in data[0]
    assert data[1]["size_bytes"] == 2097152
    assert data[1]["precision"] == "fp16"
    assert data[1]["sha256"] == SHA
    assert "gated" not in data[1]
    assert data[0]["description"] == "int8"
    assert any("templated" in s for s in skipped)
    assert any("not found on the Hub" in s for s in skipped)
    assert all(headers == {"Authorization": "Bearer t0k"} for _, headers in calls)
    assert sum(1 for c in changes if c.startswith("dit-int8.safetensors")) == 5


def test_fill_is_idempotent():
    lookup = fill.HubLookup(fetch=_fetch([]))
    once, _, _ = fill.fill_text(YAML, lookup)
    twice, changes, _ = fill.fill_text(once, lookup)

    assert twice == once
    assert changes == []


def test_infer_precision_needs_exactly_one_token():
    assert fill.infer_precision("qwen_fp8_scaled.safetensors") == "fp8"
    assert fill.infer_precision("model-bf16-int8.safetensors") is None
    assert fill.infer_precision("plain.safetensors") is None


def test_token_comes_from_the_environment():
    assert fill.token_from_env({"HF_TOKEN": "a"}) == "a"
    assert fill.token_from_env({"HUGGING_FACE_HUB_TOKEN": "b"}) == "b"
    assert fill.token_from_env({}) is None


def test_main_writes_files_and_dry_run_does_not(tmp_path):
    target = tmp_path / "models.yml"
    target.write_text(YAML, encoding="utf-8")

    assert fill.main([str(target), "--dry-run"], fetch=_fetch([]), env={}) == 0
    assert target.read_text(encoding="utf-8") == YAML
    assert fill.main([str(target)], fetch=_fetch([]), env={}) == 0
    assert "size_bytes: 3221225472" in target.read_text(encoding="utf-8")
