import { KIT_KIND_PLURALS, isTaken, type AcceptedKinds } from './kitCounts';
import { kitPathLabel } from './kitPath';
import type { KitKey } from './kitMarker';
import { KIT_ITEM_KINDS, type KitDetail, type KitItem, type KitItemKind, type KitRow } from './types';

export type KitChipState = 'pending' | 'ok' | 'skipped' | 'item-deleted' | 'file-deleted' | 'kit-deleted';

export type KitDirectory = ReadonlyMap<string, KitDetail | null>;

export interface KitChip {
	state: KitChipState;
	bare: boolean;
	kitId: string;
	itemId: string | null;
	label: string;
	handle: string | null;
	itemName: string | null;
	kind: KitItemKind | null;
	thumbnails: string[];
	count: number | null;
	of: number | null;
	line: string | null;
	struck: boolean;
	warns: boolean;
}

function chipBase(key: KitKey): KitChip {
	return {
		state: 'pending',
		bare: key.itemId === null,
		kitId: key.kitId,
		itemId: key.itemId,
		label: key.itemId ? `${key.kitId}.${key.itemId}` : key.kitId,
		handle: null,
		itemName: null,
		kind: null,
		thumbnails: [],
		count: null,
		of: null,
		line: null,
		struck: false,
		warns: false
	};
}

function thumbOf(item: KitItem): string | null {
	return item.thumbnail_url ?? (item.kind === 'image' ? item.url : null);
}

export function deriveKitChip(
	key: KitKey,
	directory: KitDirectory,
	accepted: AcceptedKinds,
	modelName: string | null = null
): KitChip {
	const chip = chipBase(key);
	if (!directory.has(key.kitId)) return chip;
	const kit = directory.get(key.kitId);
	if (!kit) {
		return { ...chip, state: 'kit-deleted', struck: !chip.bare, warns: true, line: 'This kit was deleted.' };
	}
	chip.handle = kit.handle;
	if (key.itemId === null) {
		const usable = kit.items.filter((item) => !item.missing && isTaken(item.kind, accepted));
		return {
			...chip,
			state: 'ok',
			label: kitPathLabel(kit.handle),
			thumbnails: kit.items
				.filter((item) => item.kind === 'image' || item.kind === 'video')
				.slice(0, 4)
				.map(thumbOf)
				.filter((url): url is string => !!url),
			count: usable.length,
			of: kit.items.length
		};
	}
	const item = kit.items.find((candidate) => candidate.id === key.itemId);
	if (!item) {
		return {
			...chip,
			state: 'item-deleted',
			label: `${kit.handle}.${key.itemId}`,
			struck: true,
			warns: true,
			line: `An item of ${kit.name} was removed. Its chip is left out.`
		};
	}
	const base: KitChip = {
		...chip,
		label: kitPathLabel(kit.handle, item.name),
		itemName: item.name,
		kind: item.kind,
		thumbnails: thumbOf(item) ? [thumbOf(item) as string] : []
	};
	if (item.missing) {
		return { ...base, state: 'file-deleted', warns: true, line: `${item.name} is a deleted file and is left out.` };
	}
	if (!isTaken(item.kind, accepted)) {
		const subject = modelName ?? 'This model';
		return {
			...base,
			state: 'skipped',
			warns: true,
			line: `${subject} does not take ${item.kind}. ${item.name} is skipped.`
		};
	}
	return { ...base, state: 'ok' };
}

export function chipPopoverTitle(chip: KitChip): string | null {
	if (chip.state === 'skipped' && chip.itemName) return `${chip.itemName} is skipped`;
	if (chip.state === 'file-deleted' && chip.itemName) return `${chip.itemName} is a deleted file`;
	if (chip.state === 'item-deleted') return 'This item was removed';
	if (chip.state === 'kit-deleted') return 'This kit was deleted';
	return null;
}

export type SessionChange =
	| { kind: 'deleted'; name: string }
	| {
			kind: 'changed';
			name: string;
			kitRenamed: { from: string; to: string } | null;
			itemsRenamed: { from: string; to: string }[];
			itemsRemoved: string[];
	  };

export function sessionChange(row: KitRow, directory: KitDirectory): SessionChange | null {
	if (!directory.has(row.kit)) return null;
	const current = directory.get(row.kit);
	const snapshot = row.snapshot;
	if (!current) return { kind: 'deleted', name: snapshot?.name ?? row.kit };
	if (!snapshot) return null;
	const kitRenamed = snapshot.handle !== current.handle ? { from: snapshot.handle, to: current.handle } : null;
	const itemsRenamed: { from: string; to: string }[] = [];
	const itemsRemoved: string[] = [];
	for (const before of snapshot.items) {
		const now = current.items.find((item) => item.id === before.id);
		if (!now) itemsRemoved.push(before.name);
		else if (now.name !== before.name) itemsRenamed.push({ from: before.name, to: now.name });
	}
	if (!kitRenamed && itemsRenamed.length === 0 && itemsRemoved.length === 0) return null;
	return { kind: 'changed', name: current.name, kitRenamed, itemsRenamed, itemsRemoved };
}

export function removedItemIds(row: KitRow, directory: KitDirectory): string[] {
	const current = directory.get(row.kit);
	if (!current || !row.snapshot) return [];
	return row.snapshot.items.filter((before) => !current.items.some((item) => item.id === before.id)).map((item) => item.id);
}

export interface SkippedNote {
	kind: KitItemKind;
	kindLabel: string;
	names: string[];
}

export interface KitCite {
	key: KitKey;
	accepted: AcceptedKinds;
}

export function skippedNotes(cites: readonly KitCite[], directory: KitDirectory): SkippedNote[] {
	const byKind = new Map<KitItemKind, string[]>();
	const note = (kind: KitItemKind, name: string) => {
		const names = byKind.get(kind) ?? [];
		if (!names.includes(name)) names.push(name);
		byKind.set(kind, names);
	};
	for (const { key, accepted } of cites) {
		const kit = directory.get(key.kitId);
		if (!kit) continue;
		const items = key.itemId ? kit.items.filter((item) => item.id === key.itemId) : kit.items;
		for (const item of items) if (!item.missing && !isTaken(item.kind, accepted)) note(item.kind, item.name);
	}
	return KIT_ITEM_KINDS.filter((kind) => byKind.has(kind)).map((kind) => ({
		kind,
		kindLabel: KIT_KIND_PLURALS[kind],
		names: byKind.get(kind) ?? []
	}));
}
