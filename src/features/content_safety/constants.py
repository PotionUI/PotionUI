from typing import Dict

RESTRICTED_GROUP_ID = "restricted_content"

POLICY_ALLOWED = "allowed"
POLICY_BLUR = "blur"
POLICY_BLOCKED = "blocked"

POLICIES = (POLICY_ALLOWED, POLICY_BLUR, POLICY_BLOCKED)

STRICTNESS: Dict[str, int] = {POLICY_ALLOWED: 0, POLICY_BLUR: 1, POLICY_BLOCKED: 2}

SETTING_POLICY = "content_policy_nsfw"
SETTING_BANNED_WORDS = "content_banned_words"
SETTING_VIDEO_FRAMES = "content_video_sample_frames"
SETTING_THRESHOLD = "media_nsfw_blur_threshold"

DEFAULT_THRESHOLD = 0.6
DEFAULT_VIDEO_FRAMES = 5
MAX_VIDEO_FRAMES = 8

STATE_SAFE = "safe"
STATE_FLAGGED = "flagged"
STATE_UNRATED = "unrated"

EVENT_BANNED_PROMPT = "banned_prompt"
EVENT_OUTPUT_BLOCKED = "output_blocked"
EVENT_CHECK_UNAVAILABLE = "check_unavailable"

ERROR_BANNED_PROMPT = "banned_prompt"
ERROR_CONTENT_BLOCKED = "content_blocked"
ERROR_CHECK_UNAVAILABLE = "content_check_unavailable"
