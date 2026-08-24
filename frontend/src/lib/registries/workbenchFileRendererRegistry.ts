import { createRegistry } from './registry';
import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';

/**
 * file_type -> Svelte preview component, resolved by `Workbench.svelte` for
 * media types it doesn't have a dedicated branch for. Mirrors
 * `fields/registry.ts`'s static/lazy entry shape so plugins can register a
 * preview component the same way they register a field component.
 *
 * Every registered component receives whatever props/events the specific
 * media file needs (e.g. `{ url, comparisonUrl, isComparing }`) - callers
 * treat the resolved component generically via `<svelte:component>`.
 */
export type WorkbenchFileRendererEntry =
	| { kind: 'static'; component: any }
	| { kind: 'lazy'; pluginId: string; asset: string };

const registry = createRegistry<WorkbenchFileRendererEntry>('workbench-file-renderer');

const resolvedCache = new Map<string, any | null>();

export function registerWorkbenchFileRenderer(
	fileType: string,
	entry: { component: any } | { pluginId: string; asset: string }
): void {
	if ('component' in entry) {
		registry.register(fileType, { kind: 'static', component: entry.component });
	} else {
		registry.register(fileType, { kind: 'lazy', pluginId: entry.pluginId, asset: entry.asset });
		resolvedCache.delete(fileType);
	}
}

export function unregisterWorkbenchFileRenderer(fileType: string): void {
	registry.unregister(fileType);
	resolvedCache.delete(fileType);
}

/** Resolve `fileType` to its component, falling back to `image`'s if unregistered. */
export async function resolveWorkbenchFileRenderer(fileType: string): Promise<any | null> {
	const key = registry.has(fileType) ? fileType : 'image';

	if (resolvedCache.has(key)) {
		return resolvedCache.get(key) ?? null;
	}

	const entry = registry.get(key);
	if (!entry) return null;

	if (entry.kind === 'static') {
		resolvedCache.set(key, entry.component);
		return entry.component;
	}

	const component = await resolvePluginComponent(entry.pluginId, entry.asset);
	resolvedCache.set(key, component);
	return component;
}

export function hasWorkbenchFileRenderer(fileType: string): boolean {
	return registry.has(fileType);
}

export function listWorkbenchFileTypes(): string[] {
	return registry.keys();
}
