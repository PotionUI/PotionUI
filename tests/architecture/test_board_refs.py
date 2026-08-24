"""Guard against board-tracker references leaking into tracked source.

Board references — one of a small set of uppercase prefixes (BE, FE, CMB,
OB, MT, DOC), a hyphen, then digits — are ticket identifiers from an
external planning tool. A comment or user-facing string should encode *the
constraint* a ticket produced, not the ticket itself: the number means
nothing to someone reading the code later, and it rots the moment the
tracker's history is renumbered or pruned. A prior sweep stripped 400+ such
references from the tree; this test is the guard that keeps them from
creeping back in — without it the next sweep is just resetting the clock
again.

This is a whole-repo-tree walk from ``ROOT``, not a walk of a curated list
of directories: anywhere a text file can live is in scope unless it's
explicitly exempted below. The previous version of this guard swept a fixed
``SCOPED_DIRS`` tuple plus a one-off ``SCOPED_FILES`` entry, which left every
other root-level file (``README.md``, ``LICENSE``, anything under
``.claude/``, ...) permanently unscanned — not because anyone decided those
were safe, just because nothing ever added them to the list. Whole-tree walk
with named exemptions means new root-level files are covered by default;
the burden is on an exemption to justify itself, not on someone remembering
to add a new file to a scope list.

``vendor/`` is deliberately exempt (via ``SKIP_DIRS``): its provenance
headers and ``NOTICE.md`` cite upstream commit/PR identifiers, which are
licensing provenance records (legally load-bearing), not board refs — a
different thing this guard has no business touching.

``CLAUDE.md`` (the root file only, not any file with that name anywhere in
the tree) is also exempt: it's maintainer-authored project documentation
that legitimately cites a past ref as a historical baseline (e.g. an
``npm run check`` baseline "as of FE-49") — prose about the project's
history, not source.

This file is exempt from its own check by construction (see
``_candidate_files``): it necessarily *describes* the pattern it looks for,
and a guard that fails on its own docstring is broken.

The check walks files with the standard library only (``re`` + ``pathlib``
+ ``os.walk``), exactly like ``test_layering.py``, so it carries no
third-party dependency and runs anywhere the repo is checked out.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THIS_FILE = Path(__file__).resolve()

# Root file exempt from the sweep for the reason given in the module
# docstring: maintainer-authored prose that legitimately cites a past ref as
# historical baseline. Only the root file - not any file named CLAUDE.md
# elsewhere in the tree (none currently exist, and this isn't designed to
# blanket-exempt them if they show up).
CLAUDE_MD = ROOT / "CLAUDE.md"

# Directory names pruned during the walk - matched by name, not path, so
# e.g. a nested "dist" anywhere is skipped the same as a top-level one.
# Anything not listed here is fully scanned; in particular src, frontend/src,
# content (presets, plugins, automation), docs, scripts, tests, docker,
# .github, and .claude all stay in scope exactly as before, just without
# needing to be named in an inclusion list.
SKIP_DIRS = {
    "__pycache__",
    "node_modules",
    "venv",
    ".git",
    ".svelte-kit",
    "htmlcov",
    # Plugin frontend build output (``plugins/*/frontend/dist``) is generated,
    # minified, and rebuilt from source by scripts/build-plugins.mjs - not
    # something to hand-edit for hygiene, and not interesting to scan either.
    "dist",
    # Playwright run output (gitignored, regenerated every run: traces,
    # screenshots, error-context dumps that quote spec/test names verbatim).
    ".playwright-artifacts",
    "test-results",
    "playwright-report",
    # Vite's dependency-optimizer cache - generated, rebuilt on demand, not
    # source (see feedback_vite_autorewrite in project memory re: autoRewrite;
    # unrelated to this guard, just another reason this dir is noise).
    ".vite",
    # Third-party code this project doesn't author - see module docstring.
    "vendor",
    # Not present in this checkout but a named future concern: generated
    # model-onboarding scratch output, same shape as storage/models below.
    "models_onboarding",
    # Generated media/output storage and downloaded model weights. Already
    # excluded from matches by TEXT_SUFFIXES (binary formats), but pruning
    # the directory outright avoids walking tens of thousands of files that
    # can never match anyway - see the timing note in the module docstring
    # history (PR description / commit message), not repeated here as a
    # board ref.
    "storage",
    "models",
    # IDE/agent-tool scratch directories - not source, never legitimately
    # carry a board ref worth guarding.
    ".idea",
    ".codex",
    ".codex-test-shims",
}

# Extensions worth scanning as text. Deliberately excludes binaries/media
# (models, images, video) and generated/lock artifacts (``*.map``, lockfiles).
TEXT_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".svelte",
    ".md",
    ".yml",
    ".yaml",
    ".json",
    ".txt",
    ".cfg",
    ".ini",
    ".sh",
    ".toml",
}

# Anchored on the real shape - known prefix, a literal hyphen, digits, word
# boundaries on both sides - so it does not fire on a hex colour, a hash
# fragment, or an unrelated identifier that merely contains one of these
# letter groups.
BOARD_REF_RE = re.compile(r"\b(?:BE|FE|CMB|OB|MT|DOC)-\d+\b")


def _candidate_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        # Prune in place *before* os.walk descends, so skipped subtrees
        # (vendor/, storage/, node_modules/, ...) are never even listed,
        # not just filtered out after the fact.
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        base = Path(dirpath)
        for name in filenames:
            f = base / name

            if f.suffix not in TEXT_SUFFIXES:
                continue
            if f.resolve() == THIS_FILE:
                continue
            if f.resolve() == CLAUDE_MD:
                continue

            yield f


def test_no_board_tracker_references():
    violations: list[str] = []
    for f in _candidate_files():
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if BOARD_REF_RE.search(line):
                violations.append(f"{f.relative_to(ROOT)}:{lineno}: {line.strip()}")

    assert not violations, (
        "board-tracker references (BE-/FE-/CMB-/OB-/MT-/DOC-<number>) found in "
        "tracked source - strip the ref and keep only the constraint it "
        "describes (delete the whole comment if nothing useful remains):\n"
        + "\n".join(violations)
    )


def test_candidate_files_discovers_new_root_level_files():
    """Regression guard for the whole-tree-walk switch.

    A root-level file that lives under none of the old ``SCOPED_DIRS``, and
    is not one of the exemptions (``CLAUDE.md``, this file, anything under a
    ``SKIP_DIRS`` name), must now be discovered. Before the whole-tree-walk
    change, a file like this at repo root was silently never scanned.
    """
    import uuid

    marker_name = f"_board_ref_regression_probe_{uuid.uuid4().hex}.md"
    probe_path = ROOT / marker_name

    # Built from concatenated parts so no board-ref-shaped literal is
    # committed anywhere in this file - see the constraint this guard exists
    # to enforce; the probe content itself must not violate it in source.
    prefix = "".join(["F", "E"])
    fake_ref_line = f"See {prefix}-{'8' * 5} for context.\n"

    try:
        with open(probe_path, "w", encoding="utf-8") as fh:
            fh.write(fake_ref_line)

        discovered = {p.resolve() for p in _candidate_files()}
        assert probe_path.resolve() in discovered, (
            f"expected whole-tree walk to discover a new root-level file "
            f"({marker_name}), but it was not in the candidate set - the "
            f"discovery mechanism regressed back to a scoped subset"
        )
    finally:
        if probe_path.exists():
            probe_path.unlink()
