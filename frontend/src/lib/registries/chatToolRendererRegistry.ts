import { createRegistry } from './registry';
import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';

/**
 * tool_name -> Svelte component for a per-tool chat rendering surface.
 * Registered entries receive `{ execution, toolMeta, expanded }` props:
 *   - execution: the ToolExecution (arguments, result, pending_approval, ...)
 *   - toolMeta: `{ icon, label }` from the backend tool catalog (may be null)
 *   - expanded: whether the step is expanded (true) or rendering as an
 *     approval preview (also true today; kept for future compact use)
 *
 * An entry is either a `static` core component or a `lazy` plugin-hosted
 * component (registered from a manifest's `renderers: [{kind: "chat.tool", ...}]`
 * entry), resolved on demand via `plugin-api/componentResolver` — mirrors
 * `artifactRendererRegistry.ts`. No core consumer currently calls `.resolve()`
 * (the transcript renders tool status as chips — ChatToolChip — and pending
 * approvals go through ApprovalDock, neither of which is per-tool-rendered);
 * this stays as the `registries.chatTool` plugin API surface (`host.ts`).
 */
export type ChatToolRendererEntry =
	| { kind: 'static'; component: any }
	| { kind: 'lazy'; pluginId: string; asset: string };

const registry = createRegistry<ChatToolRendererEntry>('chat-tool-renderer');

const resolvedCache = new Map<string, any | null>();

function registerChatToolRenderer(
	toolName: string,
	entry: { component: any } | { pluginId: string; asset: string }
): void {
	if ('component' in entry) {
		registry.register(toolName, { kind: 'static', component: entry.component });
	} else {
		registry.register(toolName, { kind: 'lazy', pluginId: entry.pluginId, asset: entry.asset });
		resolvedCache.delete(toolName);
	}
}

function unregisterChatToolRenderer(toolName: string): void {
	registry.unregister(toolName);
	resolvedCache.delete(toolName);
}

/** Resolve `toolName` to its renderer component. Returns `null` if unregistered. */
async function resolveChatToolRenderer(toolName: string): Promise<any | null> {
	if (resolvedCache.has(toolName)) {
		return resolvedCache.get(toolName) ?? null;
	}

	const entry = registry.get(toolName);
	if (!entry) return null;

	if (entry.kind === 'static') {
		resolvedCache.set(toolName, entry.component);
		return entry.component;
	}

	const component = await resolvePluginComponent(entry.pluginId, entry.asset);
	resolvedCache.set(toolName, component);
	return component;
}

export const chatToolRendererRegistry = {
	register: registerChatToolRenderer,
	unregister: unregisterChatToolRenderer,
	resolve: resolveChatToolRenderer,
	has: (toolName: string) => registry.has(toolName),
	keys: () => registry.keys()
};
