"""Prompt import format parsers.

Each parser has the shape `(data: bytes | str, *, filename: str | None) ->
list[ParsedPrompt]` and never touches the repository - `operations.importing`
is the only place a `ParsedPrompt` becomes a persisted row. `PARSERS` maps
the format key `detect_format` (or an explicit caller override) returns to
its parser.
"""
from src.features.prompt_database.importing.csv_format import parse_styles_csv
from src.features.prompt_database.importing.detect import detect_format
from src.features.prompt_database.importing.image_format import parse_image
from src.features.prompt_database.importing.json_format import parse_style_json
from src.features.prompt_database.importing.lines_format import parse_lines
from src.features.prompt_database.importing.models import ParsedPrompt
from src.features.prompt_database.importing.yaml_format import parse_wildcard_yaml

PARSERS = {
    "styles_csv": parse_styles_csv,
    "style_json": parse_style_json,
    "wildcard_yaml": parse_wildcard_yaml,
    "lines": parse_lines,
    "image": parse_image,
}

__all__ = [
    "ParsedPrompt",
    "PARSERS",
    "detect_format",
    "parse_styles_csv",
    "parse_style_json",
    "parse_wildcard_yaml",
    "parse_lines",
    "parse_image",
]
