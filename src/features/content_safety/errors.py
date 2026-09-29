from src.features.content_safety.constants import ERROR_BANNED_PROMPT, ERROR_CHECK_UNAVAILABLE


class ContentPolicyRefusal(Exception):
    code = "content_policy_refused"
    status_code = 422


class BannedPromptRefused(ContentPolicyRefusal):
    code = ERROR_BANNED_PROMPT
    status_code = 422


class ContentCheckUnavailable(ContentPolicyRefusal):
    code = ERROR_CHECK_UNAVAILABLE
    status_code = 503
