import type { FilterItem, FilterSource } from './types';

export interface StripSection {
	key: string;
	label: string;
	source: FilterSource;
	items: FilterItem[];
}

const SOURCE_RANK: Record<FilterSource, number> = { builtin: 0, local: 1, plugin: 2, mine: 3 };

export function isLocked(item: FilterItem): boolean {
	return item.unavailable_ops.length > 0 || item.needs_plugin !== null;
}

export function lockReason(item: FilterItem): string {
	if (item.needs_plugin) return `Needs plugin "${item.needs_plugin}" (not enabled)`;
	if (item.unavailable_ops.length > 0) return `Needs ${item.unavailable_ops.join(', ')} (not available)`;
	return '';
}

function sectionKey(item: FilterItem): string {
	if (item.source === 'mine') return 'mine';
	if (item.source === 'plugin') return `plugin:${item.plugin_id ?? ''}`;
	return `${item.source}:${item.group}`;
}

function sectionLabel(item: FilterItem): string {
	if (item.source === 'mine') return 'Mine';
	if (item.source === 'plugin') return item.plugin_id ?? 'Plugin';
	return item.group;
}

export function buildStrip(items: FilterItem[], groups: string[]): StripSection[] {
	const groupRank = new Map<string, number>();
	for (const name of groups) if (!groupRank.has(name)) groupRank.set(name, groupRank.size);
	for (const item of items) {
		if (item.source === 'builtin' || item.source === 'local') {
			if (!groupRank.has(item.group)) groupRank.set(item.group, groupRank.size);
		}
	}
	const rankOf = (item: FilterItem) =>
		item.source === 'builtin' || item.source === 'local' ? (groupRank.get(item.group) ?? 0) : 0;

	const sorted = [...items].sort(
		(a, b) =>
			SOURCE_RANK[a.source] - SOURCE_RANK[b.source] ||
			(a.source === 'plugin' ? (a.plugin_id ?? '').localeCompare(b.plugin_id ?? '') : 0) ||
			rankOf(a) - rankOf(b) ||
			(a.source === 'mine' ? 0 : a.order - b.order) ||
			a.name.localeCompare(b.name)
	);

	const sections: StripSection[] = [];
	for (const item of sorted) {
		const key = sectionKey(item);
		const last = sections[sections.length - 1];
		if (last && last.key === key) last.items.push(item);
		else sections.push({ key, label: sectionLabel(item), source: item.source, items: [item] });
	}
	return sections;
}

export function selectableOrder(sections: StripSection[]): Array<FilterItem | null> {
	const order: Array<FilterItem | null> = [null];
	for (const section of sections) {
		for (const item of section.items) if (!isLocked(item)) order.push(item);
	}
	return order;
}

export function moveSelection(
	order: Array<FilterItem | null>,
	currentId: string | null,
	key: 'next' | 'previous' | 'first' | 'last'
): FilterItem | null {
	const index = Math.max(
		0,
		order.findIndex((entry) => (entry?.id ?? null) === currentId)
	);
	if (key === 'first') return order[0];
	if (key === 'last') return order[order.length - 1];
	if (key === 'next') return order[Math.min(order.length - 1, index + 1)];
	return order[Math.max(0, index - 1)];
}
