from typing import Annotated, Any, Dict, Literal, Mapping, Tuple

from pydantic import StringConstraints

MediaType = Literal["image", "video"]
PORT_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
PortName = Annotated[str, StringConstraints(pattern=PORT_NAME_PATTERN)]

TEXT = "text"
WRITER_OUTPUT = "text"
WRITER_INPUTS: Dict[str, Tuple[str, str]] = {"images": ("Images", "image"), "videos": ("Videos", "video")}
PROMPT_INPUTS: Dict[str, str] = {"prompt": "Prompt", "negative_prompt": "Negative prompt"}
UNTITLED = {"media": "Untitled media source", "llm": "Untitled prompt writer"}


def node_label(node: Mapping[str, Any]) -> str:
    title = (node.get("title") or "").strip()
    if title:
        return title
    return UNTITLED.get(node.get("kind"), "Untitled preset node")
