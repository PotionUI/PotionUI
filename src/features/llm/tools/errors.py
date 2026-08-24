"""Shared shape for LLM tool error strings.

The convention (stated at media_values.py:13-15, honored there and in
model_values.py): an error string a tool returns to the model teaches it at
least as much as the tool's description does. Both modules already follow
the same shape without a shared helper - what was wrong, what a valid value
looks like, and (where there is a way to find out) what to call next. This
module names that shape so a new call site reaches for it instead of
reinventing a bare `f"Failed: {e}"`.
"""

from typing import Optional


def teach(problem: str, expected: str, next_step: Optional[str] = None) -> str:
    """Compose a teaching error: what was wrong, what valid looks like, and
    optionally what to do about it.

    Each argument is a complete clause (no trailing period needed) - this
    only joins them into sentences, it does not invent wording of its own.
    """
    sentences = [problem.rstrip("."), expected.rstrip(".")]
    if next_step:
        sentences.append(next_step.rstrip("."))
    return ". ".join(sentences) + "."


def unexpected(tool: str, operation: str, error: Exception) -> str:
    """Error text for an unexpected-exception catch-all.

    Names the tool and the operation that failed instead of returning a bare
    `str(e)` / `f"Failed: {e}"` with no frame, so the model at least learns
    which call broke and that retrying with the same or different arguments
    will not fix it. The exception detail is kept - for a genuine backend
    failure it can be the only useful diagnostic there is - but it is never
    returned on its own. Callers should still log `error` for a human.
    """
    return (
        f"{tool}'s {operation} failed unexpectedly: {error}. This is not "
        "something you can fix by changing your call - tell the user, or try once more."
    )
