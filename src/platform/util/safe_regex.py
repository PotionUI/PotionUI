from typing import Any

import re2

REGEX_MAX_LENGTH = 200
UNSUPPORTED_HINT = "Backreferences and lookarounds are not supported."


class InvalidRegex(ValueError):
    pass


def _options(case_sensitive: bool) -> "re2.Options":
    options = re2.Options()
    options.case_sensitive = case_sensitive
    options.log_errors = False
    return options


def compile_regex(pattern: str, case_sensitive: bool = False) -> Any:
    try:
        return re2.compile(pattern, _options(case_sensitive))
    except re2.error as exc:
        reason = exc.args[0] if exc.args else exc
        if isinstance(reason, bytes):
            reason = reason.decode("utf-8", "replace")
        raise InvalidRegex(str(reason))


def compile_user_regex(pattern: str, case_sensitive: bool = False) -> Any:
    if len(pattern) > REGEX_MAX_LENGTH:
        raise InvalidRegex(f"Regular expression is longer than {REGEX_MAX_LENGTH} characters")
    try:
        return compile_regex(pattern, case_sensitive)
    except InvalidRegex as exc:
        raise InvalidRegex(f"Invalid regular expression: {exc}. {UNSUPPORTED_HINT}")
