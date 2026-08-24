"""What a plugin declares when it contributes a chat mode.

A chat mode is the organizing concept of the LLM chat: every session is created
in exactly one mode (immutable for the session's lifetime). The mode owns the
base system prompt and the set of tools visible to the session (in addition to
global tools). Modes are registered by the chat feature (the builtin
"generation" mode) or by plugins.

The mode itself, and the error raised when two of them claim one id, live here
beside the other plugin contribution types -- the plugin registry builds a
`ChatMode` straight out of a manifest's `chat_modes:` section, and platform may
not reach into a feature to do it. The registry that serves them to the chat
lives with the chat, in `src.features.chat.modes.registry`.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union


class DuplicateChatModeError(Exception):
    """A mode with the same id is already registered."""
    pass


@dataclass
class ChatMode:
    """A registered chat mode.

    Attributes:
        id: Unique mode identifier (e.g. 'generation').
        name: Human-readable name shown in the mode selector.
        description: One-line description shown in the mode selector.
        system_prompt: Base system prompt for the mode. May contain the
            ``{{TOOL_HINTS}}`` placeholder which is substituted with the hints of
            the session's allowed tools. A callable can be provided instead of a
            string when the prompt must be re-read per request (e.g. a plugin
            mode that wants to read live configuration on each call); the builtin
            generation mode uses a plain code-owned template.
        tool_names: Names of tools explicitly included in this mode, in addition
            to tools that declare the mode via ``BaseTool.modes`` and global
            tools. Lets plugin modes borrow builtin tools.
        icon: Optional lucide icon name for the UI.
        default_route_prefixes: Frontend route prefixes that resolve to this
            mode (longest prefix wins), e.g. ["/generate"].
        resource_namespaces: Resource namespaces visible in this mode's
            @-mention dropdown. None means all registered namespaces.
        context_contributor: Optional callable ``(context_metadata, session,
            user_id) -> Optional[str]`` whose result is injected as a system
            context block before generation.
        llm_options: Sampling/thinking overrides applied on top of the LLM
            config for every call in this mode (e.g. {"think": False}).
        structured_reply: Whether ``resolve_system_prompt`` appends the
            ``## improved`` / ``## questions`` reply-contract block (see
            ``src.features.chat.reply_contract``). Defaults on; a mode whose
            own prompt already enforces a strict output document should set
            this False rather than risk the two contracts colliding.
        source: "builtin" or the registering plugin's id.
    """

    id: str
    name: str
    description: str = ""
    system_prompt: Union[str, Callable[[], str]] = ""
    tool_names: List[str] = field(default_factory=list)
    icon: Optional[str] = None
    default_route_prefixes: List[str] = field(default_factory=list)
    resource_namespaces: Optional[List[str]] = None
    context_contributor: Optional[Callable] = None
    llm_options: Dict[str, Any] = field(default_factory=dict)
    structured_reply: bool = True
    source: str = "builtin"

    def resolve_prompt_template(self) -> str:
        """Return the prompt template, invoking it when it is a callable."""
        if callable(self.system_prompt):
            return self.system_prompt() or ""
        return self.system_prompt or ""
