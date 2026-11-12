export interface BarItemWidths {
	full: number;
	icon: number;
}

export interface BarPlan {
	mode: 'inline' | 'overflow';
	iconOnly: number[];
}

export interface BarPlanInput {
	available: number;
	fixed: number;
	gap: number;
	items: readonly BarItemWidths[];
}

export function planSelectionBar({ available, fixed, gap, items }: BarPlanInput): BarPlan {
	const shown = items.map((item, index) => ({ ...item, index })).filter((item) => item.full > 0);
	const gaps = Math.max(0, shown.length - 1) * gap;
	let total = fixed + gaps + shown.reduce((sum, item) => sum + item.full, 0);
	const iconOnly: number[] = [];
	if (total <= available) return { mode: 'inline', iconOnly };
	for (const item of shown) {
		if (item.icon >= item.full) continue;
		total -= item.full - item.icon;
		iconOnly.push(item.index);
		if (total <= available) return { mode: 'inline', iconOnly };
	}
	return { mode: 'overflow', iconOnly: [] };
}
