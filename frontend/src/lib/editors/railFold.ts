export const RAIL_ICON_HEIGHT = 40;
export const RAIL_GAP = 2;
export const RAIL_DIVIDER_HEIGHT = 13;
export const RAIL_PADDING = 16;

export interface FoldInput {
	id: string;
	count: number;
	fold?: number;
}

export interface FoldMetrics {
	icon: number;
	gap: number;
	divider: number;
	padding: number;
}

export const DEFAULT_FOLD_METRICS: FoldMetrics = {
	icon: RAIL_ICON_HEIGHT,
	gap: RAIL_GAP,
	divider: RAIL_DIVIDER_HEIGHT,
	padding: RAIL_PADDING
};

export function railContentHeight(groups: readonly FoldInput[], folded: ReadonlySet<string>, metrics: FoldMetrics = DEFAULT_FOLD_METRICS): number {
	const icons = groups.reduce((sum, group) => sum + (folded.has(group.id) ? 1 : group.count), 0);
	const dividers = Math.max(0, groups.length - 1);
	const items = icons + dividers;
	if (items === 0) return 0;
	return metrics.padding + icons * metrics.icon + dividers * metrics.divider + (items - 1) * metrics.gap;
}

export function foldOrder(groups: readonly FoldInput[]): FoldInput[] {
	return groups
		.map((group, index) => ({ group, index, rank: group.fold ?? 1000 + (groups.length - index) }))
		.filter(({ group }) => group.count > 1)
		.sort((a, b) => a.rank - b.rank)
		.map(({ group }) => group);
}

export function foldGroups(groups: readonly FoldInput[], available: number, metrics: FoldMetrics = DEFAULT_FOLD_METRICS): string[] {
	const folded = new Set<string>();
	const out: string[] = [];
	if (!Number.isFinite(available) || available <= 0) return out;
	for (const group of foldOrder(groups)) {
		if (railContentHeight(groups, folded, metrics) <= available) break;
		folded.add(group.id);
		out.push(group.id);
	}
	return out;
}
