import { KIT_ITEM_KINDS, type KitCounts, type KitItem, type KitItemKind } from './types';

export const KIT_KIND_LABELS: Record<KitItemKind, string> = {
	image: 'Image',
	video: 'Video',
	audio: 'Audio',
	text: 'Text'
};

export const KIT_KIND_PLURALS: Record<KitItemKind, string> = {
	image: 'images',
	video: 'videos',
	audio: 'audio',
	text: 'texts'
};

export const KIT_KIND_ICONS: Record<KitItemKind, string> = {
	image: 'image',
	video: 'video',
	audio: 'audio',
	text: 'document'
};

export function countByKind(items: readonly Pick<KitItem, 'kind'>[]): KitCounts {
	const counts: KitCounts = {};
	for (const item of items) counts[item.kind] = (counts[item.kind] ?? 0) + 1;
	return counts;
}

export function presentKinds(counts: KitCounts): KitItemKind[] {
	return KIT_ITEM_KINDS.filter((kind) => (counts[kind] ?? 0) > 0);
}

export function totalOf(counts: KitCounts): number {
	return KIT_ITEM_KINDS.reduce((sum, kind) => sum + (counts[kind] ?? 0), 0);
}

export type AcceptedKinds = ReadonlySet<KitItemKind> | null;

export function acceptedFrom(kinds: readonly string[] | null | undefined): AcceptedKinds {
	if (!kinds) return null;
	const set = new Set<KitItemKind>(['text']);
	for (const kind of kinds) if (kind === 'image' || kind === 'video' || kind === 'audio') set.add(kind);
	return set;
}

export function isTaken(kind: KitItemKind, accepted: AcceptedKinds): boolean {
	return accepted === null || kind === 'text' || accepted.has(kind);
}

export interface Usage {
	used: KitItem[];
	skipped: KitItem[];
}

export function usageOf(items: readonly KitItem[], accepted: AcceptedKinds): Usage {
	const used: KitItem[] = [];
	const skipped: KitItem[] = [];
	for (const item of items) {
		if (item.missing || !isTaken(item.kind, accepted)) skipped.push(item);
		else used.push(item);
	}
	return { used, skipped };
}

export function mediaCountOf(items: readonly KitItem[], accepted: AcceptedKinds): number {
	return usageOf(items, accepted).used.filter((item) => item.kind !== 'text').length;
}

export function usageNote(usage: Usage): { tone: 'success' | 'warning'; text: string } {
	if (usage.skipped.length === 0) return { tone: 'success', text: 'All used' };
	return { tone: 'warning', text: `${usage.skipped.length} skipped` };
}

export function takesLabel(usage: Usage, total: number): string {
	return usage.skipped.length === 0 ? `${total}` : `${usage.used.length} of ${total}`;
}

export interface SkippedGroup {
	kind: KitItemKind;
	kindLabel: string;
	names: string[];
}

export function skippedGroups(items: readonly KitItem[], accepted: AcceptedKinds): SkippedGroup[] {
	const byKind = new Map<KitItemKind, string[]>();
	for (const item of items) {
		if (item.missing || isTaken(item.kind, accepted)) continue;
		byKind.set(item.kind, [...(byKind.get(item.kind) ?? []), item.name]);
	}
	return KIT_ITEM_KINDS.filter((kind) => byKind.has(kind)).map((kind) => ({
		kind,
		kindLabel: KIT_KIND_PLURALS[kind],
		names: byKind.get(kind) ?? []
	}));
}

export function formatItemCount(total: number): string {
	return `${total} ${total === 1 ? 'item' : 'items'}`;
}

export function acceptedSentence(accepted: AcceptedKinds): string {
	if (accepted === null) return 'Text is added to the prompt where you call it.';
	const media = (['image', 'video', 'audio'] as const).filter((kind) => accepted.has(kind));
	if (media.length === 0) return 'This model takes no reference media. Text is added to the prompt where you call it.';
	const names = media.map((kind) => KIT_KIND_PLURALS[kind]);
	const list =
		names.length === 1 ? names[0] : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
	return `This model takes ${list}. Text is added to the prompt where you call it.`;
}
