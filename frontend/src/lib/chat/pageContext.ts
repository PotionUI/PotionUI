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

/**
 * A handler may report back how a confirmed tool result actually landed,
 * instead of the host assuming "handled" means "applied as narrated" -
 * e.g. a proposal built for a draft the page has since moved on from is
 * reported `'stale'` rather than silently mutating the wrong thing.
 */
export interface ToolAppliedOutcome {
	status: 'applied' | 'stale' | 'partial' | 'noop';
	/** Shown in place of the backend's own narration when status isn't 'applied'. */
	message?: string;
}

export type ToolAppliedHandler = (result: Record<string, unknown>) => void | ToolAppliedOutcome;

function isToolAppliedOutcome(value: unknown): value is ToolAppliedOutcome {
	return !!value && typeof value === 'object' && typeof (value as { status?: unknown }).status === 'string';
}

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
 * Run every handler registered for `toolName` with `result`. Returns false if
 * no handler was registered (the caller should run its own hardcoded dispatch
 * for that tool in that case); otherwise returns the last `ToolAppliedOutcome`
 * a handler reported, or `true` when none reported one (a handler that
 * returns nothing, or a value that isn't a `ToolAppliedOutcome`, is still
 * "handled" — just without an outcome for the caller to act on).
 */
export function dispatchToolApplied(
	toolName: string,
	result: Record<string, unknown>
): ToolAppliedOutcome | boolean {
	const handlers = toolAppliedHandlers.get(toolName);
	if (!handlers || handlers.size === 0) return false;
	let outcome: ToolAppliedOutcome | undefined;
	for (const handler of handlers) {
		const handlerResult = handler(result);
		if (isToolAppliedOutcome(handlerResult)) outcome = handlerResult;
	}
	return outcome ?? true;
}

/** Drop all registrations. Called on SPA user switch (see `applyIdentityGuard`). */
export function resetPageContext(): void {
	contextProviders.clear();
	toolAppliedHandlers.clear();
	modeDeclarations.set([]);
}
