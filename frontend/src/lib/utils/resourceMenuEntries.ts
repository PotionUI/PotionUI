import { kindLabel, mediaItemKey, type PromptResourceKind } from './promptResources';

export interface ResourceMenuEntry {
	id: string;
	label: string;
	value: string;
	kind: PromptResourceKind;
	url: string | undefined;
	order: number;
}

export function resourceItemDisplayName(item: unknown, itemKey: string): string {
	if (item && typeof item === 'object') {
		const record = item as Record<string, unknown>;
		for (const field of ['name', 'label'] as const) {
			const value = record[field];
			if (typeof value === 'string' && value.trim()) return value.trim();
		}
	}
	return itemKey.replace(/\\/g, '/').split('/').pop() || itemKey;
}

export function resourceMenuEntries(items: readonly unknown[], kind: PromptResourceKind): ResourceMenuEntry[] {
	const entries: ResourceMenuEntry[] = [];
	items.forEach((item, order) => {
		const id = mediaItemKey(item);
		if (!id) return;
		const url = item && typeof item === 'object' ? (item as Record<string, unknown>).url : undefined;
		entries.push({
			id,
			label: resourceItemDisplayName(item, id),
			value: kindLabel(kind),
			kind,
			url: typeof url === 'string' ? url : undefined,
			order
		});
	});
	return entries;
}
