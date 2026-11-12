import type { KitRow, KitRowSnapshot } from './types';

export const KIT_KEY_PREFIX = 'kit:';

export interface KitKey {
	kitId: string;
	itemId: string | null;
}

export function encodeKitKey(kitId: string, itemId?: string | null): string {
	return itemId ? `${KIT_KEY_PREFIX}${kitId}/${itemId}` : `${KIT_KEY_PREFIX}${kitId}`;
}

export function parseKitKey(itemKey: string): KitKey | null {
	if (!itemKey.startsWith(KIT_KEY_PREFIX)) return null;
	const rest = itemKey.slice(KIT_KEY_PREFIX.length);
	const match = /^(kt_[0-9a-f]{8})(?:\/(it_[0-9a-f]{8}))?$/.exec(rest);
	if (!match) return null;
	return { kitId: match[1], itemId: match[2] ?? null };
}

export function isKitKey(itemKey: string): boolean {
	return parseKitKey(itemKey) !== null;
}

export function isKitRow(value: unknown): value is KitRow {
	if (!value || typeof value !== 'object') return false;
	const record = value as Record<string, unknown>;
	return typeof record.kit === 'string' && record.kit !== '' && !('path' in record) && !('url' in record);
}

export function splitKitRows<T>(value: readonly T[]): { media: T[]; kits: KitRow[] } {
	const media: T[] = [];
	const kits: KitRow[] = [];
	for (const entry of value) {
		if (isKitRow(entry)) kits.push(entry);
		else media.push(entry);
	}
	return { media, kits };
}

export function withKitRows<T>(media: readonly T[], kits: readonly KitRow[]): (T | KitRow)[] {
	return [...media, ...kits];
}

export function kitRowIds(kits: readonly KitRow[]): string[] {
	return kits.map((row) => row.kit);
}

export function addKitRow(kits: readonly KitRow[], row: KitRow): KitRow[] {
	return kits.some((existing) => existing.kit === row.kit) ? [...kits] : [...kits, row];
}

export function removeKitRow(kits: readonly KitRow[], kitId: string): KitRow[] {
	return kits.filter((row) => row.kit !== kitId);
}

export function snapshotOf(kit: { name: string; handle: string; items: { id: string; name: string }[] }): KitRowSnapshot {
	return {
		name: kit.name,
		handle: kit.handle,
		items: kit.items.map((item) => ({ id: item.id, name: item.name }))
	};
}
