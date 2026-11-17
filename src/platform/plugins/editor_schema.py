import json
from pathlib import Path
from typing import Any, Iterable, List, Optional

from src.platform.util.path_resolution import is_within

MAX_SCHEMA_BYTES = 256 * 1024
STRUCTURAL_TYPES = frozenset({"tab"})


class EditorSchemaError(ValueError):
    pass


def schema_path(plugin_dir: Path, relative: str) -> Path:
    dist = (Path(plugin_dir) / "frontend" / "dist").resolve()
    candidate = (dist / relative).resolve()
    if not is_within(dist, candidate):
        raise EditorSchemaError(f"schema '{relative}' is outside frontend/dist")
    return candidate


def read_tool_schema(plugin_dir: Path, relative: str) -> List[dict]:
    path = schema_path(plugin_dir, relative)
    try:
        if path.stat().st_size > MAX_SCHEMA_BYTES:
            raise EditorSchemaError(f"schema '{relative}' is larger than {MAX_SCHEMA_BYTES // 1024} KiB")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except EditorSchemaError:
        raise
    except FileNotFoundError:
        raise EditorSchemaError(f"schema '{relative}' was not found in frontend/dist") from None
    except (OSError, ValueError) as exc:
        raise EditorSchemaError(f"schema '{relative}' could not be read: {exc}") from None
    if isinstance(payload, dict) and isinstance(payload.get("fields"), list):
        payload = payload["fields"]
    if not isinstance(payload, list):
        raise EditorSchemaError(f"schema '{relative}' must be a list of fields")
    return payload


def validate_tool_schema(fields: Any, known_types: Optional[Iterable[str]], where: str) -> None:
    known = None if known_types is None else set(known_types) | STRUCTURAL_TYPES
    seen: set = set()

    def visit(items: Any, trail: str) -> None:
        if not isinstance(items, list):
            raise EditorSchemaError(f"{where}: {trail or 'schema'} must be a list of fields")
        for index, field in enumerate(items):
            label = f"field {index + 1}"
            if not isinstance(field, dict):
                raise EditorSchemaError(f"{where}: {label} must be an object")
            type_name = field.get("type")
            name = field.get("name")
            label = f"field '{name}'" if isinstance(name, str) and name else label
            if not isinstance(type_name, str) or not type_name:
                raise EditorSchemaError(f"{where}: {label} needs a 'type'")
            if known is not None and type_name not in known:
                raise EditorSchemaError(f"{where}: {label} has unknown field type '{type_name}'")
            children = field.get("children")
            if children is not None:
                visit(children, label)
            elif isinstance(name, str) and name:
                if name in seen:
                    raise EditorSchemaError(f"{where}: {label} is declared twice")
                seen.add(name)

    visit(fields, "")
