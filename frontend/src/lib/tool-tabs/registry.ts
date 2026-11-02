import { derived, get, writable, type Readable } from 'svelte/store';
import type { ToolKindDefinition, ToolTabComponent } from './types';

const kinds = writable<Map<string, ToolKindDefinition>>(new Map());

export function registerToolKind(definition: ToolKindDefinition): () => void {
	kinds.update((current) => {
		if (current.get(definition.tool) === definition) return current;
		const next = new Map(current);
		next.set(definition.tool, definition);
		return next;
	});
	return () => {
		kinds.update((current) => {
			if (current.get(definition.tool) !== definition) return current;
			const next = new Map(current);
			next.delete(definition.tool);
			return next;
		});
	};
}

function orderOf(definition: ToolKindDefinition): number {
	return definition.order ?? (definition.pluginId ? 1000 : 100);
}

export const toolKinds: Readable<ToolKindDefinition[]> = derived(kinds, ($kinds) =>
	[...$kinds.values()].sort((a, b) => orderOf(a) - orderOf(b) || a.label.localeCompare(b.label))
);

export const coreToolKinds: Readable<ToolKindDefinition[]> = derived(toolKinds, ($kinds) =>
	$kinds.filter((definition) => !definition.pluginId)
);

export const pluginToolKinds: Readable<ToolKindDefinition[]> = derived(toolKinds, ($kinds) =>
	$kinds.filter((definition) => !!definition.pluginId)
);

export function getToolKind(tool: string): ToolKindDefinition | undefined {
	return get(kinds).get(tool);
}

export function toolKindStore(tool: string): Readable<ToolKindDefinition | undefined> {
	return derived(kinds, ($kinds) => $kinds.get(tool));
}

export async function resolveToolComponent(definition: ToolKindDefinition): Promise<ToolTabComponent | null> {
	if (definition.component) return definition.component;
	if (!definition.load) return null;
	const loaded = await definition.load();
	if (!loaded) return null;
	return 'default' in loaded ? loaded.default : loaded;
}

export function toolLabel(tool: string): string {
	return getToolKind(tool)?.label ?? tool;
}
