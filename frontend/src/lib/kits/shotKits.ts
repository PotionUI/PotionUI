import { encodeKitKey, parseKitKey } from './kitMarker';
import type { LoadedKit } from './promptKits';
import type { KitItem } from './types';
import { resourceMarkerRegex } from '$lib/utils/promptResources';

export interface ShotKitEntry {
	loaded: LoadedKit;
	item: KitItem;
	count: number;
	marker: string;
	path: string;
}

export interface ShotKitUsage {
	used: ShotKitEntry[];
	unused: ShotKitEntry[];
}

function markerFor(field: string, kitId: string, itemId: string): string {
	return `@[${field}:${encodeKitKey(kitId, itemId)}]`;
}

export function shotKitUsage(texts: readonly (string | null | undefined)[], loaded: readonly LoadedKit[]): ShotKitUsage {
	const counts = new Map<string, number>();
	for (const text of texts) {
		if (!text || !text.includes('kit:')) continue;
		for (const match of text.matchAll(resourceMarkerRegex())) {
			const key = parseKitKey(match[2]);
			if (!key) continue;
			const entry = loaded.find((candidate) => candidate.kit.id === key.kitId);
			if (!entry) continue;
			const ids = key.itemId ? [key.itemId] : entry.kit.items.filter((item) => item.kind !== 'text').map((item) => item.id);
			for (const id of ids) counts.set(`${key.kitId}/${id}`, (counts.get(`${key.kitId}/${id}`) ?? 0) + 1);
		}
	}
	const used: ShotKitEntry[] = [];
	const unused: ShotKitEntry[] = [];
	for (const entry of loaded) {
		for (const item of entry.kit.items) {
			const count = counts.get(`${entry.kit.id}/${item.id}`) ?? 0;
			const row: ShotKitEntry = {
				loaded: entry,
				item,
				count,
				marker: markerFor(entry.field, entry.kit.id, item.id),
				path: `@kits.${entry.kit.handle}.${item.name}`
			};
			(count > 0 ? used : unused).push(row);
		}
	}
	return { used, unused };
}
