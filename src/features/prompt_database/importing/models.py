"""One prompt half as read off an external source, before it becomes a row.

A format parser never touches the repository or fires hooks - it turns raw
bytes into a list of these. `operations.importing.import_prompts` is the only
place a `ParsedPrompt` is turned into a `PromptRequest` and persisted.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ParsedPrompt:
    text: str
    usage_hint: str
    name: Optional[str] = None
    group_id: Optional[str] = None
    model_name: Optional[str] = None
    base_model: Optional[str] = None
    cfg_scale: Optional[float] = None
    steps: Optional[int] = None
    sampler: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    seed: Optional[int] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
