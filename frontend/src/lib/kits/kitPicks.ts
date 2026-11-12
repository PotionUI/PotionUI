import type { LibraryItem } from '$lib/services/api/library';
import { defaultItemNames } from './kitNaming';
import type { KitItem, KitItemKind, NewKitItem } from './types';

export interface KitPick {
	key: string;
	kind: KitItemKind;
	label: string;
	thumbnail: string | null;
	mediaRef: string | null;
	text: string | null;
}

export function kindOfMediaType(mediaType: string | null | undefined): KitItemKind | null {
	const value = (mediaType ?? '').toLowerCase();
	if (value === 'image' || value === 'video' || value === 'audio') return value;
	return null;
}

export function pickFromLibraryItem(item: LibraryItem): KitPick | null {
	const kind = kindOfMediaType(item.media_type);
	if (!kind) return null;
	return {
		key: item.id,
		kind,
		label: item.original_filename || item.filename,
		thumbnail: item.thumbnail_small ?? (kind === 'image' ? item.url : null),
		mediaRef: item.id,
		text: null
	};
}

export function picksFromLibraryItems(items: readonly LibraryItem[]): KitPick[] {
	return items.map(pickFromLibraryItem).filter((pick): pick is KitPick => pick !== null);
}

export function picksNames(picks: readonly KitPick[], taken: Iterable<string> = []): string[] {
	return defaultItemNames(
		picks.map((pick) => pick.kind),
		taken
	);
}

export function toNewItems(picks: readonly KitPick[], names: readonly string[]): NewKitItem[] {
	return picks.map((pick, index) => {
		const base: NewKitItem = { kind: pick.kind, name: names[index] };
		if (pick.kind === 'text') return { ...base, text: pick.text ?? '' };
		return { ...base, media_ref: pick.mediaRef ?? undefined };
	});
}

export function takenNames(items: readonly Pick<KitItem, 'name'>[]): string[] {
	return items.map((item) => item.name);
}

export function remainingRoom(count: number, limit: number): number {
	return Math.max(0, limit - count);
}

export function describeLimit(count: number, adding: number, limit: number): string | null {
	if (count + adding <= limit) return null;
	const room = remainingRoom(count, limit);
	return room === 0
		? `A kit holds ${limit} items and this one is full.`
		: `A kit holds ${limit} items. There is room for ${room} more.`;
}
