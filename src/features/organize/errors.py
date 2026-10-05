from typing import Any, Dict, List, Optional


class OrganizeError(Exception):
    def __init__(self, code: str, message: str, status_code: int, extra: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.extra = extra or {}


def rule_not_found() -> OrganizeError:
    return OrganizeError("rule_not_found", "Rule not found", 404)


def invalid_rule(problems: List[Dict[str, str]]) -> OrganizeError:
    message = problems[0]["message"] if problems else "The rule is not valid"
    return OrganizeError("invalid_rule", message, 422, {"problems": problems})


def rule_cap_reached(cap: int) -> OrganizeError:
    return OrganizeError("rule_cap_reached", f"You can have at most {cap} rules", 409)


def organize_paused() -> OrganizeError:
    return OrganizeError("organize_paused", "Auto-organize is paused by an admin", 423)
