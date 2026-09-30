from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from src.features.model_layouts.readers.base import (
    ReaderContext,
    ReaderResult,
    find_child_ci,
    read_bounded_text,
)

DEFAULT_FILES = ("webui-user.sh", "webui-user.bat")

ARG_TO_MODEL_TYPE = {
    "--ckpt-dir": "checkpoint",
    "--lora-dir": "lora",
    "--vae-dir": "vae",
    "--embeddings-dir": "embedding",
    "--esrgan-models-path": "upscaler",
    "--realesrgan-models-path": "upscaler",
    "--text-encoder-dir": "text_encoder",
    "--controlnet-dir": "controlnet",
}

_BAT_SET_RE = re.compile(r"^\s*set\s+(\"?)COMMANDLINE_ARGS=(.*)$", re.IGNORECASE)
_SH_SET_RE = re.compile(r"^\s*(?:export\s+)?COMMANDLINE_ARGS=(.*)$")
_BAT_SELF_RE = re.compile(r"%COMMANDLINE_ARGS%", re.IGNORECASE)
_SH_SELF_RE = re.compile(r"\$\{COMMANDLINE_ARGS\}|\$COMMANDLINE_ARGS")


def tokenize(value: str, posix: bool) -> List[str]:
    tokens: List[str] = []
    current: List[str] = []
    quote: Optional[str] = None
    started = False
    i = 0
    while i < len(value):
        char = value[i]
        if quote:
            if char == quote:
                quote = None
            elif posix and quote == '"' and char == "\\" and i + 1 < len(value) and value[i + 1] in '"\\$':
                i += 1
                current.append(value[i])
            else:
                current.append(char)
        elif char == '"' or (posix and char == "'"):
            quote = char
            started = True
        elif posix and char == "\\" and i + 1 < len(value):
            i += 1
            current.append(value[i])
            started = True
        elif char.isspace():
            if current or started:
                tokens.append("".join(current))
            current, started = [], False
        else:
            current.append(char)
            started = True
        i += 1
    if current or started:
        tokens.append("".join(current))
    return tokens


def _sh_value(rest: str) -> str:
    rest = rest.strip()
    if rest[:1] in ('"', "'"):
        quote = rest[0]
        i = 1
        while i < len(rest):
            if rest[i] == "\\" and quote == '"':
                i += 2
                continue
            if rest[i] == quote:
                return rest[1:i]
            i += 1
        return rest[1:]
    return re.split(r"\s#", rest, maxsplit=1)[0]


def extract_commandline_args(text: str, bat: bool) -> Optional[str]:
    current: Optional[str] = None
    for line in text.splitlines():
        stripped = line.strip()
        if bat:
            if stripped[:4].lower() == "rem " or stripped.startswith("::"):
                continue
            match = _BAT_SET_RE.match(line)
            if not match:
                continue
            value = match.group(2).rstrip()
            if match.group(1) and value.endswith('"'):
                value = value[:-1]
            value = _BAT_SELF_RE.sub(lambda _: current or "", value)
        else:
            if stripped.startswith("#"):
                continue
            match = _SH_SET_RE.match(line)
            if not match:
                continue
            value = _SH_SELF_RE.sub(lambda _: current or "", _sh_value(match.group(1)))
        current = value
    return current


def parse_args(tokens: List[str]) -> Dict[str, str]:
    found: Dict[str, str] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("--"):
            name, equals, value = token.partition("=")
            if not equals and i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                value = tokens[i + 1]
                i += 1
            if value:
                found[name] = value
        i += 1
    return found


def read(ctx: ReaderContext, file: Optional[str], result: ReaderResult) -> None:
    names = (file,) if file else DEFAULT_FILES
    args: Dict[str, str] = {}
    for name in names:
        path = find_child_ci(ctx.install_dir, name)
        if path is None:
            continue
        result.source_file = result.source_file or str(path)
        text = read_bounded_text(path, result)
        if text is None:
            continue
        bat = Path(name).suffix.lower() in (".bat", ".cmd")
        line = extract_commandline_args(text, bat)
        if line:
            args.update(parse_args(tokenize(line, posix=not bat)))
    if not args:
        return

    data_dir = ctx.resolve(args.get("--data-dir"), ctx.install_dir, result)
    models_dir = ctx.resolve(args.get("--models-dir"), ctx.install_dir, result)
    if models_dir is None and data_dir:
        models_dir = str(Path(data_dir) / "models")
    if models_dir:
        ctx.set_primary(result, models_dir)

    for arg, model_type in ARG_TO_MODEL_TYPE.items():
        resolved = ctx.resolve(args.get(arg), ctx.install_dir, result)
        if resolved:
            ctx.add_entry(result, model_type, resolved, arg)
    if data_dir and "--embeddings-dir" not in args:
        ctx.add_entry(result, "embedding", str(Path(data_dir) / "embeddings"), "--data-dir")
