"""Typed per-feature builders composed by `src.bootstrap.container`.

Each module here exposes a frozen ``<Feature>Deps`` (everything the feature
needs from the rest of the process), a frozen ``<Feature>Components`` (what it
contributes back), and a ``build_<feature>(deps)`` function. A builder receives
every collaborator at call time and returns fully-wired objects: nothing it
returns is completed by a later attribute assignment.

These live under `src.bootstrap` rather than next to the feature because they
are wiring, and `src.features` may not import the composition root.
"""

from src.bootstrap.composition.chat import ChatComponents, ChatDeps, build_chat

__all__ = ["ChatComponents", "ChatDeps", "build_chat"]
