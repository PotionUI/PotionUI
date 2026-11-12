import pytest

from src.platform.runtime.native.text_encoders import tokenization


def test_the_bundled_tokenizer_is_loaded_through_the_guarded_import(monkeypatch):
    calls = []

    class FakeTokenizer:
        @staticmethod
        def from_pretrained(path, local_files_only):
            calls.append((path, local_files_only))
            return "tok"

    monkeypatch.setattr(tokenization, "guarded_from", lambda name, attr: (calls.append((name, attr)), FakeTokenizer)[1])
    assert tokenization._load_tokenizer("qwen3_tokenizer", "Qwen2TokenizerFast") == "tok"
    assert calls[0] == ("transformers", "Qwen2TokenizerFast")
    assert calls[1][1] is True


def test_a_failing_guarded_import_surfaces_the_real_error(monkeypatch):
    def boom(name, attr):
        raise ImportError("cannot import name 'logging' from 'huggingface_hub'")

    monkeypatch.setattr(tokenization, "guarded_from", boom)
    with pytest.raises(ImportError, match="logging"):
        tokenization._load_tokenizer("qwen3_tokenizer", "Qwen2TokenizerFast")
