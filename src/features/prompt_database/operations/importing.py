"""Turn parsed external prompts into rows, and roll saved prompts back into a styles.csv.

Every import goes through `create_prompt` - the same before/after_save hooks
that fire for a hand-authored prompt fire here too. A parser failure or a
per-entry save failure is recorded against that one file/entry and never
sinks the rest of the batch.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.features.prompt_database.collaborators import PromptDatabaseCollaborators
from src.features.prompt_database.dto import PromptRequest
from src.features.prompt_database.importing import PARSERS, ParsedPrompt, detect_format
from src.features.prompt_database.operations.mutations import create_prompt
from src.features.prompt_database.records import Prompt
from src.features.segments.dto import RichSegment

logger = logging.getLogger(__name__)

IMPORT_SOURCE_PROVIDER = "import"
EXPORT_PAGE_SIZE = 200


@dataclass
class ImportOutcome:
    imported: int = 0
    skipped: int = 0
    total: int = 0
    items: List[Dict[str, Any]] = field(default_factory=list)
    files: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "imported": self.imported, "skipped": self.skipped, "total": self.total,
            "items": self.items, "files": self.files,
        }


def _request_from(
    parsed: ParsedPrompt, *, fmt: str, filename: str, model_name: Optional[str], base_model: Optional[str],
) -> PromptRequest:
    metadata: Dict[str, Any] = dict(parsed.metadata)
    metadata["import_format"] = fmt
    metadata["import_file"] = filename
    if parsed.seed is not None:
        metadata["seed"] = parsed.seed
    return PromptRequest(
        name=parsed.name,
        usage_hint=parsed.usage_hint,
        segments=[RichSegment(content=parsed.text)],
        source_provider=IMPORT_SOURCE_PROVIDER,
        source_group_id=parsed.group_id,
        model_name=parsed.model_name or model_name,
        base_model=parsed.base_model or base_model,
        cfg_scale=parsed.cfg_scale,
        steps=parsed.steps,
        sampler=parsed.sampler,
        width=parsed.width,
        height=parsed.height,
        tags=list(parsed.tags),
        metadata=metadata,
    )


async def import_prompts(
    collaborators: PromptDatabaseCollaborators, user_id: str,
    files: Sequence[Tuple[str, bytes]], *,
    format: Optional[str] = None,
    model_name: Optional[str] = None, base_model: Optional[str] = None,
) -> ImportOutcome:
    outcome = ImportOutcome()
    for filename, data in files:
        fmt = format or detect_format(filename, data)
        file_summary: Dict[str, Any] = {"filename": filename, "format": fmt, "imported": 0, "skipped": 0}

        parser = PARSERS.get(fmt)
        if parser is None:
            file_summary["reason"] = f"unknown_format:{fmt}"
            outcome.files.append(file_summary)
            continue

        try:
            parsed_prompts = parser(data, filename=filename)
        except Exception as exc:
            logger.warning("Prompt import parse failed for %s (%s): %s", filename, fmt, exc)
            file_summary["reason"] = str(exc) or exc.__class__.__name__
            outcome.files.append(file_summary)
            continue

        if not parsed_prompts:
            file_summary["reason"] = "no_metadata" if fmt == "image" else "empty"
            outcome.files.append(file_summary)
            continue

        for parsed in parsed_prompts:
            outcome.total += 1
            if not parsed.text.strip():
                outcome.skipped += 1
                file_summary["skipped"] += 1
                continue
            try:
                request = _request_from(parsed, fmt=fmt, filename=filename, model_name=model_name, base_model=base_model)
                prompt = await create_prompt(collaborators, user_id, request)
            except Exception as exc:
                logger.warning("Prompt import save failed for an entry in %s: %s", filename, exc)
                outcome.skipped += 1
                file_summary["skipped"] += 1
                continue
            outcome.imported += 1
            file_summary["imported"] += 1
            outcome.items.append(prompt.to_dict())
        outcome.files.append(file_summary)
    return outcome


def _iter_all_prompts(collaborators: PromptDatabaseCollaborators, user_id: str, collection_id: Optional[str]):
    offset = 0
    while True:
        page = collaborators.repository.get_all(
            user_id=user_id, limit=EXPORT_PAGE_SIZE, offset=offset, collection_id=collection_id,
        )
        if not page:
            return
        yield from page
        if len(page) < EXPORT_PAGE_SIZE:
            return
        offset += EXPORT_PAGE_SIZE


def export_styles_csv(
    collaborators: PromptDatabaseCollaborators, user_id: str, *, collection_id: Optional[str] = None,
) -> str:
    import csv
    import io

    grouped: Dict[str, Dict[str, Prompt]] = {}
    ungrouped: List[Prompt] = []
    for prompt in _iter_all_prompts(collaborators, user_id, collection_id):
        if prompt.source_group_id:
            grouped.setdefault(prompt.source_group_id, {})[prompt.usage_hint or "positive"] = prompt
        else:
            ungrouped.append(prompt)

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["name", "prompt", "negative_prompt"])
    for pair in grouped.values():
        positive = pair.get("positive")
        negative = pair.get("negative")
        reference = positive or negative
        name = (reference.name or reference.display_name) if reference else ""
        writer.writerow([
            name,
            positive.flattened_text if positive else "",
            negative.flattened_text if negative else "",
        ])
    for prompt in ungrouped:
        name = prompt.name or prompt.display_name
        if prompt.usage_hint == "negative":
            writer.writerow([name, "", prompt.flattened_text])
        else:
            writer.writerow([name, prompt.flattened_text, ""])
    return buffer.getvalue()
