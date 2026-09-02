"""One prompt per non-empty line; `#`-prefixed lines are wildcard-file comments."""
from typing import List, Optional, Union

from src.features.prompt_database.importing.models import ParsedPrompt


def parse_lines(data: Union[bytes, str], *, filename: Optional[str] = None) -> List[ParsedPrompt]:
    text = data.decode("utf-8") if isinstance(data, bytes) else data
    results: List[ParsedPrompt] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        results.append(ParsedPrompt(text=stripped, usage_hint="positive"))
    return results
