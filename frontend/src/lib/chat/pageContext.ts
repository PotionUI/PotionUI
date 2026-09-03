/**
 * Generic host-API surface backing `window.__potionui.chat`
 * (`$lib/plugin-api/host.ts`). A plugin-hosted page (e.g. the ComfyUI import
 * wizard) uses this to feed the chat assistant page-scoped context, take over
 * the resolved chat mode while it's the active surface, and react to a tool
 * result the user approved — without UnifiedAIChat.svelte knowing the plugin
 * exists.
 *
 * Module-scope state here is per-browser-session content (which page/mode is
 * active), not per-user data, but it's reset on an identity switch anyway via
 * `resetPageContext()` (see `applyIdentityGuard` in `$lib/stores/auth.ts`) so
 * a stale registration from a previous user's session never lingers.
 */
import { derived, writable } from 'svelte/store';

export type ContextProvider = () => Record<string, unknown> | null;
export type ToolAppliedHandler = (result: Record<string, unknown>) => void;

interface ModeDeclaration {
	id: number;
	modeId: string;
}

const contextProviders = new Map<string, ContextProvider>();
const toolAppliedHandlers = new Map<string, Set<ToolAppliedHandler>>();

let nextDeclarationId = 1;
const modeDeclarations = writable<ModeDeclaration[]>([]);

/** The most recently declared mode, or null when nothing has declared one. */
export const declaredMode = derived(modeDeclarations, (declarations) =>
	declarations.length > 0 ? declarations[declarations.length - 1].modeId : null
);

/**
 * Register a context provider under `key`. On every chat send,
 * UnifiedAIChat merges `{ [key]: provider() }` for each registered provider
 * into `context_metadata` (a null result is skipped, not sent as `null`).
 * Returns an unregister function.
 */
export function provideContext(key: string, provider: ContextProvider): () => void {
	contextProviders.set(key, provider);
	return () => {
		if (contextProviders.get(key) === provider) contextProviders.delete(key);
	};
}

/** Merge every registered provider's current output into one object. */
export function collectProvidedContext(): Record<string, unknown> {
	const merged: Record<string, unknown> = {};
	for (const [key, provider] of contextProviders) {
		const result = provider();
		if (result !== null && result !== undefined) merged[key] = result;
	}
	return merged;
}

/**
 * Declare `modeId` as the chat mode for as long as this declaration is
 * active. While one or more declarations are active, the most recently
 * declared one wins over the route-prefix resolution
 * (`resolveModeForRoute`); unregistering the last active declaration
 * restores route-based resolution. Returns an unregister function.
 */
export function declareMode(modeId: string): () => void {
	const id = nextDeclarationId++;
	modeDeclarations.update((declarations) => [...declarations, { id, modeId }]);
	return () => {
		modeDeclarations.update((declarations) => declarations.filter((d) => d.id !== id));
	};
}

/**
 * Register a handler for a confirmed/approved tool result. Every handler
 * registered for `toolName` runs (in registration order) before any
 * hardcoded dispatch for that tool. Returns an unregister function.
 */
export function onToolApplied(toolName: string, handler: ToolAppliedHandler): () => void {
	let handlers = toolAppliedHandlers.get(toolName);
	if (!handlers) {
		handlers = new Set();
		toolAppliedHandlers.set(toolName, handlers);
	}
	handlers.add(handler);
	return () => {
		const current = toolAppliedHandlers.get(toolName);
		if (!current) return;
		current.delete(handler);
		if (current.size === 0) toolAppliedHandlers.delete(toolName);
	};
}

/**
 * Run every handler registered for `toolName` with `result`. Returns true if
 * at least one handler was registered (and ran) — the caller should skip its
 * own hardcoded dispatch for that tool when this is true.
 */
export function dispatchToolApplied(toolName: string, result: Record<string, unknown>): boolean {
	const handlers = toolAppliedHandlers.get(toolName);
	if (!handlers || handlers.size === 0) return false;
	for (const handler of handlers) handler(result);
	return true;
}

/** Drop all registrations. Called on SPA user switch (see `applyIdentityGuard`). */
export function resetPageContext(): void {
	contextProviders.clear();
	toolAppliedHandlers.clear();
	modeDeclarations.set([]);
}
