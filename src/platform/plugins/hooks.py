"""
Hook system for plugin architecture.

This module provides the hook execution infrastructure for the PotionUI
plugin system. Hooks allow plugins to intercept and modify behavior at key
points in the application lifecycle.

Hooks are no longer a fixed enum. Each domain (generation, chat, users, ...)
declares its own hook points next to the manager that owns them, via
`hooks_registry.declare(...)`, which returns a namespace of plain string
constants (typo-safe at the call site, open-ended for plugins). Plugins may
also declare their own hook points via a manifest's `provides_hooks:` list.

The hook system supports:
- Backend hooks (executed in Python)
- Frontend hooks (registered for reference, executed in JavaScript)
- Chain execution (each handler can modify the context)
- Error handling and result tracking
"""

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    TYPE_CHECKING,
    Union,
)
import logging

if TYPE_CHECKING:
    from src.platform.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)

# Reserved `HookContext.data` key carrying awaitables a synchronous hook handler
# wants the (async) call site to wait on before it proceeds. The hook chain
# itself stays synchronous; a handler that needs to defer work schedules it and
# leaves the awaitables here, and an async caller drains them via
# `await_hook_blocking_waits`. Used by the automation hook bridge's opt-in
# "wait for the triggered run" mode (src/features/automation/triggers/hook_bridge.py).
HOOK_BLOCKING_WAITS_KEY = "__hook_blocking_waits__"


async def await_hook_blocking_waits(context: Optional["HookContext"]) -> None:
    """Await (and clear) any awaitables a handler left under `HOOK_BLOCKING_WAITS_KEY`.

    Never raises: each awaitable is expected to bound itself (timeout, error
    handling) so one slow or failed continuation can't block or crash the caller.
    """
    if context is None:
        return
    waits = context.data.pop(HOOK_BLOCKING_WAITS_KEY, None)
    if not waits:
        return
    await asyncio.gather(*waits, return_exceptions=True)

def execute_hook(plugins: "PluginRegistry", hook, data: dict) -> Tuple[dict, bool]:
    """Run `hook` with `data` and report the resulting context and veto flag.

    Returns the (possibly plugin-mutated) context data and whether a plugin set
    `blocked` on it. A blocked result is the caller's cue to raise its own
    domain exception with `block_reason`.
    """
    context, _ = plugins.execute_hook(hook, initial_data=data)
    blocked = context.data.get("blocked", False)
    return context.data, blocked


HookKind = Literal["backend", "frontend"]

# Fields a `specs`/`declare_one` payload dict may carry, beyond `description`.
_SPEC_FIELDS = {"description", "payload", "mutable", "use_when", "example"}


@dataclass(frozen=True)
class HookSpec:
    """Declaration of a single hook point."""
    name: str
    type: HookKind
    description: str = ""
    payload: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    mutable: tuple[str, ...] = ()
    use_when: tuple[str, ...] = ()
    example: str = ""


def _normalize_spec_kwargs(
    description: str = "",
    payload: Optional[Mapping[str, Mapping[str, str]]] = None,
    mutable: Optional[Sequence[str]] = None,
    use_when: Optional[Sequence[str]] = None,
    example: str = "",
) -> Dict[str, Any]:
    """Normalize the loosely-typed spec fields into the plain shapes HookSpec expects."""
    return {
        "description": description,
        "payload": {k: dict(v) for k, v in (payload or {}).items()},
        "mutable": tuple(mutable or ()),
        "use_when": tuple(use_when or ()),
        "example": example,
    }


def _specs_conflict(existing: HookSpec, incoming: HookSpec) -> bool:
    """True if two specs for the same hook name differ in their type (a hard conflict)."""
    return existing.type != incoming.type


class HookRegistry:
    """
    Open registry of hook points.

    Domains declare the hooks they fire via `declare(...)`, which is
    idempotent for identical redeclarations (e.g. module re-import) but
    raises on a conflicting redeclaration (same name, different type).
    Redeclaration with the same type but different documentation fields
    (description/payload/mutable/use_when/example) is allowed and simply
    keeps the last-declared spec (logged at debug level).
    """

    def __init__(self):
        self._specs: Dict[str, HookSpec] = {}

    def _upsert(self, full_name: str, spec: HookSpec) -> None:
        existing = self._specs.get(full_name)
        if existing is not None and _specs_conflict(existing, spec):
            raise ValueError(
                f"Conflicting hook declaration for '{full_name}': "
                f"already declared as '{existing.type}', now '{spec.type}'"
            )
        if existing is not None and existing != spec:
            logger.debug(f"Redeclaring hook '{full_name}' with updated spec (last-declare-wins)")
        self._specs[full_name] = spec

    def declare(
        self,
        domain: str,
        type: HookKind,
        *names: str,
        descriptions: Optional[Dict[str, str]] = None,
        specs: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> SimpleNamespace:
        """
        Declare hook points under `domain` and return a namespace of the
        full hook name strings, keyed by an attribute derived from `names`
        (dots replaced with underscores, e.g. "settings.panel" -> attribute
        `settings_panel`, value "provider.settings.panel").

        Args:
            domain: Dot-namespaced prefix (e.g. "generation", "chat.session")
            type: "backend" or "frontend"
            *names: Suffixes appended to `domain` (e.g. "before_start")
            descriptions: Optional per-name human descriptions
            specs: Optional per-name dict of structured docs (description,
                payload, mutable, use_when, example). Values here override
                `descriptions` for the same name. Unknown keys raise ValueError.

        Returns:
            SimpleNamespace mapping attribute -> full hook name string
        """
        descriptions = descriptions or {}
        specs = specs or {}
        namespace: Dict[str, str] = {}

        for name in names:
            full_name = f"{domain}.{name}"
            attr = name.replace(".", "_")

            name_spec = specs.get(name, {})
            unknown_keys = set(name_spec) - _SPEC_FIELDS
            if unknown_keys:
                raise ValueError(
                    f"Unknown spec key(s) for hook '{full_name}': {sorted(unknown_keys)}"
                )

            kwargs = _normalize_spec_kwargs(**name_spec)
            if "description" not in name_spec:
                kwargs["description"] = descriptions.get(name, "")

            spec = HookSpec(name=full_name, type=type, **kwargs)
            self._upsert(full_name, spec)
            namespace[attr] = full_name

        return SimpleNamespace(**namespace)

    def declare_one(
        self,
        full_name: str,
        type: HookKind,
        description: str = "",
        payload: Optional[Mapping[str, Mapping[str, str]]] = None,
        mutable: Optional[Sequence[str]] = None,
        use_when: Optional[Sequence[str]] = None,
        example: str = "",
    ) -> str:
        """
        Declare a single already-namespaced hook (e.g. a plugin's `provides_hooks:`
        entry, which is already a full dotted name). Returns the name unchanged.
        """
        kwargs = _normalize_spec_kwargs(
            description=description,
            payload=payload,
            mutable=mutable,
            use_when=use_when,
            example=example,
        )
        spec = HookSpec(name=full_name, type=type, **kwargs)
        self._upsert(full_name, spec)
        return full_name

    def get(self, name: str) -> Optional[HookSpec]:
        """Look up a declared hook spec by its full name, or None if undeclared."""
        return self._specs.get(name)

    def all(self) -> List[HookSpec]:
        """All declared hook specs."""
        return list(self._specs.values())


# Module-level singleton - domain hook modules declare into this at import time.
hooks_registry = HookRegistry()


@dataclass
class HookContext:
    """Context passed to hook handlers"""
    hook_name: str
    plugin_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from context data"""
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value in context data"""
        self.data[key] = value

    def has(self, key: str) -> bool:
        """Check if a key exists in context data"""
        return key in self.data

    def update(self, updates: Dict[str, Any]) -> None:
        """Update multiple values in context data"""
        self.data.update(updates)


@dataclass
class HookResult:
    """Result from a hook execution"""
    plugin_id: str
    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    modified: bool = False  # Whether the hook modified the context


class HookChain:
    """
    Executes hooks in chain, allowing each to modify the context.

    The chain pattern allows multiple plugins to process the same hook,
    with each plugin receiving the context potentially modified by previous
    plugins in the chain.
    """

    def __init__(self):
        self._handlers: Dict[str, List[tuple[str, Callable]]] = {}

    def register(
        self,
        hook_name: str,
        plugin_id: str,
        handler: Callable[[HookContext], HookContext]
    ) -> None:
        """
        Register a handler for a specific hook.

        Args:
            hook_name: Name of the hook (e.g., "generation.before_start")
            plugin_id: Unique identifier of the plugin registering the handler
            handler: Callable that accepts HookContext and returns HookContext
        """
        if hook_name not in self._handlers:
            self._handlers[hook_name] = []

        # Check if this plugin already has a handler for this hook
        for idx, (existing_plugin_id, _) in enumerate(self._handlers[hook_name]):
            if existing_plugin_id == plugin_id:
                # Replace existing handler
                self._handlers[hook_name][idx] = (plugin_id, handler)
                logger.debug(f"Replaced handler for {hook_name} from plugin {plugin_id}")
                return

        # Add new handler
        self._handlers[hook_name].append((plugin_id, handler))
        logger.debug(f"Registered handler for {hook_name} from plugin {plugin_id}")

    def unregister(self, hook_name: str, plugin_id: str) -> bool:
        """
        Unregister a handler for a specific hook.

        Args:
            hook_name: Name of the hook
            plugin_id: Unique identifier of the plugin

        Returns:
            True if handler was found and removed, False otherwise
        """
        if hook_name not in self._handlers:
            return False

        original_length = len(self._handlers[hook_name])
        self._handlers[hook_name] = [
            (pid, handler) for pid, handler in self._handlers[hook_name]
            if pid != plugin_id
        ]

        removed = len(self._handlers[hook_name]) < original_length
        if removed:
            logger.debug(f"Unregistered handler for {hook_name} from plugin {plugin_id}")

        return removed

    def execute(
        self,
        hook_name: str,
        context: Optional[HookContext] = None,
        initial_data: Optional[Dict[str, Any]] = None
    ) -> tuple[HookContext, List[HookResult]]:
        """
        Execute all handlers for a hook in sequence.

        Args:
            hook_name: Name of the hook to execute
            context: Optional pre-built context to use
            initial_data: Optional initial data to populate the context with

        Returns:
            Tuple of (final_context, list_of_results)
        """
        return self._run_handlers(
            hook_name,
            self._handlers.get(hook_name, []),
            context=context,
            initial_data=initial_data,
        )

    def execute_for_plugin(
        self,
        hook_name: str,
        plugin_id: str,
        context: Optional[HookContext] = None,
        initial_data: Optional[Dict[str, Any]] = None
    ) -> tuple[HookContext, List[HookResult]]:
        """
        Execute only `plugin_id`'s own handler for a hook, ignoring every other
        plugin's.

        For hooks that address one plugin rather than broadcast to all of them:
        the payload's subject and the handler's owner are the same plugin, so
        fanning out to the whole chain would run every other plugin's handler
        against a subject that isn't theirs.

        Args:
            hook_name: Name of the hook to execute
            plugin_id: Plugin whose handler should run
            context: Optional pre-built context to use
            initial_data: Optional initial data to populate the context with

        Returns:
            Tuple of (final_context, list_of_results) - the results list is
            empty when that plugin registered no handler for this hook.
        """
        handlers = [
            (pid, handler) for pid, handler in self._handlers.get(hook_name, [])
            if pid == plugin_id
        ]
        return self._run_handlers(
            hook_name, handlers, context=context, initial_data=initial_data
        )

    def _run_handlers(
        self,
        hook_name: str,
        handlers: List[tuple[str, Callable]],
        context: Optional[HookContext] = None,
        initial_data: Optional[Dict[str, Any]] = None
    ) -> tuple[HookContext, List[HookResult]]:
        """Run a resolved handler list against one context, collecting results."""
        if context is None:
            context = HookContext(
                hook_name=hook_name,
                plugin_id="system",
                data=initial_data or {}
            )

        if not handlers:
            return context, []

        results: List[HookResult] = []

        for plugin_id, handler in handlers:
            try:
                # Create a plugin-specific context copy
                plugin_context = HookContext(
                    hook_name=hook_name,
                    plugin_id=plugin_id,
                    data=context.data.copy(),
                    metadata=context.metadata.copy()
                )

                # Execute the handler
                logger.debug(f"Executing hook {hook_name} for plugin {plugin_id}")
                result_context = handler(plugin_context)

                # Check if context was modified
                modified = result_context.data != context.data

                # Update the main context with any changes
                if modified:
                    context.data = result_context.data
                    context.metadata.update(result_context.metadata)

                # Record successful execution
                results.append(HookResult(
                    plugin_id=plugin_id,
                    success=True,
                    data=result_context.data,
                    modified=modified
                ))

                logger.debug(
                    f"Hook {hook_name} for plugin {plugin_id} executed successfully "
                    f"(modified: {modified})"
                )

            except Exception as e:
                # Log error but continue with other handlers
                error_msg = f"Error executing hook {hook_name} for plugin {plugin_id}: {e}"
                logger.error(error_msg, exc_info=True)

                results.append(HookResult(
                    plugin_id=plugin_id,
                    success=False,
                    error=error_msg,
                    modified=False
                ))

        return context, results

    def clear_handlers(self, hook_name: Optional[str] = None) -> None:
        """
        Clear handlers for a specific hook or all hooks.

        Args:
            hook_name: Name of the hook to clear, or None to clear all hooks
        """
        if hook_name is None:
            self._handlers.clear()
            logger.debug("Cleared all hook handlers")
        else:
            if hook_name in self._handlers:
                del self._handlers[hook_name]
                logger.debug(f"Cleared handlers for {hook_name}")
