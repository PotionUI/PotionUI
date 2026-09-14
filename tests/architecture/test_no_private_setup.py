import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PLACEHOLDER_USERS = {"u", "user", "me", "you", "name", "alice", "bob", "secret", "example", "someone", "runner"}
PLACEHOLDER_MOUNTS = {"storage", "shared", "nas", "data", "models", "weights", "media", "disk", "external"}

GENERIC = [
    re.compile(r"/home/(?P<user>[a-z][a-z0-9_-]*)/"),
    re.compile(r"/mnt/(?P<mount>[a-z0-9_-]+)/"),
    re.compile(r"\bmaintainer'?s? (?:own |dev |personal )?(?:box|machine|server|rig|laptop|desktop|gpu|setup)\b", re.I),
    re.compile(r"\buser's dev (?:gpu|box|machine)\b", re.I),
    re.compile(r"\bmaintainer has (?:no|a|an)\b", re.I),
]

TEXT_SUFFIXES = {".py", ".ts", ".js", ".mjs", ".svelte", ".css", ".md", ".yml", ".yaml", ".json", ".toml", ".txt", ".sh", ".cmd", ".html"}
SKIP_PARTS = {"node_modules", "venv", "dist", "vocab.json", "package-lock.json"}


def local_patterns():
    extra = ROOT / ".git" / "info" / "private-patterns"
    if not extra.is_file():
        return []
    return [re.compile(re.escape(line.strip()), re.I) for line in extra.read_text(encoding="utf-8").splitlines() if line.strip()]


def tracked_text_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    for raw in out.split(b"\0"):
        if not raw:
            continue
        path = Path(raw.decode("utf-8", "surrogateescape"))
        if path.suffix not in TEXT_SUFFIXES or SKIP_PARTS & set(path.parts):
            continue
        yield path


def _is_hit(pattern, match):
    groups = match.groupdict() if pattern.groups else {}
    if groups.get("user") in PLACEHOLDER_USERS or groups.get("mount") in PLACEHOLDER_MOUNTS:
        return False
    return True


def test_tracked_files_carry_no_private_setup_details():
    patterns = GENERIC + local_patterns()
    hits = []
    for rel in tracked_text_files():
        try:
            text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in patterns:
            for match in pattern.finditer(text):
                if _is_hit(pattern, match):
                    hits.append(f"{rel}:{text.count(chr(10), 0, match.start()) + 1}")
    assert not hits, "private setup details in tracked files:\n" + "\n".join(hits)
