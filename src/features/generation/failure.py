from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from src.features.generation.error_classification import (
    classification_for_code,
    classify_error_text,
    classify_generation_error,
)
from src.pipelines.outputs import ErrorGenerationOutput
from src.platform.database.rows import dt_iso

DETAIL_LIMIT_CHARS = 64 * 1024
_TRUNCATION_MARKER = "\n\n[... {omitted} characters truncated ...]\n\n"


@dataclass(frozen=True)
class GenerationFailure:
    error_code: str
    message: str
    hints: Tuple[str, ...] = ()
    raw_error: Optional[str] = None
    detail: Optional[str] = None
    failed_pipe_id: Optional[str] = None
    failed_pipe_name: Optional[str] = None
    failed_at_step: Optional[str] = None

    @property
    def hint(self) -> str:
        return format_hint(self.hints)

    @property
    def user_message(self) -> str:
        return compose_user_message(self.message, self.hints)

    @property
    def stored_detail(self) -> Optional[str]:
        parts = [part for part in (self.raw_error, self.detail) if part]
        if not parts:
            return None
        return cap_detail("\n\n".join(parts))

    def columns(self) -> Dict[str, Optional[str]]:
        return {
            "error_code": self.error_code,
            "error_message": self.message,
            "error_user_message": self.user_message,
            "error_detail": self.stored_detail,
            "failed_pipe_id": self.failed_pipe_id,
            "failed_pipe_name": self.failed_pipe_name,
            "failed_at_step": self.failed_at_step,
        }

    def public_payload(self, generation_id: str) -> Dict[str, Any]:
        return public_error_payload(generation_id, self.error_code, self.message, self.hint)

    def admin_payload(self) -> Dict[str, Any]:
        return {
            "detail": self.stored_detail,
            "failed_pipe_id": self.failed_pipe_id,
            "failed_pipe_name": self.failed_pipe_name,
            "failed_at_step": self.failed_at_step,
        }


def format_hint(hints) -> str:
    return "\n".join(f"- {hint}" for hint in hints)


def compose_user_message(message: str, hints) -> str:
    hint = format_hint(hints)
    return f"{message}\n\n{hint}" if hint else message


def public_error_payload(generation_id: str, error_code: Optional[str], message: Optional[str], hint: Optional[str]) -> Dict[str, Any]:
    return {
        "status": "failed",
        "error_code": error_code,
        "message": message,
        "hint": hint,
        "error_id": generation_id,
    }


def cap_detail(text: str, limit: int = DETAIL_LIMIT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    omitted = len(text) - head - tail
    return text[:head] + _TRUNCATION_MARKER.format(omitted=omitted) + text[-tail:]


def failure_from_output(output: ErrorGenerationOutput) -> GenerationFailure:
    if output.error_code:
        classification = classification_for_code(output.error_code)
    else:
        classification = classify_error_text(output.error)
    hints = tuple(output.hints) if output.hints else tuple(classification.suggestions)
    pipe_key = output.pipe_key or output.pipe_name
    return GenerationFailure(
        error_code=classification.category,
        message=output.message or classification.summary,
        hints=hints,
        raw_error=output.error,
        detail=output.detail,
        failed_pipe_id=pipe_key,
        failed_pipe_name=output.pipe_name,
        failed_at_step=output.failed_at_step,
    )


def failure_from_exception(exc: BaseException, detail: Optional[str] = None) -> GenerationFailure:
    classification = classify_generation_error(exc)
    return GenerationFailure(
        error_code=classification.category,
        message=classification.summary,
        hints=tuple(classification.suggestions),
        raw_error=f"{type(exc).__name__}: {exc}",
        detail=detail,
    )


def apply_failure(output: ErrorGenerationOutput, failure: GenerationFailure) -> ErrorGenerationOutput:
    output.error_code = failure.error_code
    output.message = failure.message
    output.hints = list(failure.hints)
    return output


def failure_report(generation) -> Dict[str, Any]:
    classification = classification_for_code(generation.error_code)
    return {
        "generation_id": generation.id,
        "error_id": generation.id,
        "error_code": generation.error_code,
        "message": generation.error_message,
        "hint": format_hint(classification.suggestions) if generation.error_code else None,
        "detail": generation.error_detail,
        "failed_pipe_id": generation.failed_pipe_id,
        "failed_pipe_name": generation.failed_pipe_name,
        "failed_at_step": generation.failed_at_step,
        "occurred_at": dt_iso(generation.completed_at),
    }
