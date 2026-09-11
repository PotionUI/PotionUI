import type { PresetStyle } from '$lib/types/api';

const DEFAULT_CATEGORY = 'General';

function categoryOf(style: PresetStyle): string {
	return style.category || DEFAULT_CATEGORY;
}

export interface StyleCategoryOption {
	id: string;
	label: string;
	count: number;
}

/** `All` first (total count), then each category in first-appearance order —
 *  same chip idiom as the model-type row in AddDownloadModal.svelte. Counts
 *  are always against the full `styles` list, independent of the text filter. */
export function categoryOptions(styles: readonly PresetStyle[]): StyleCategoryOption[] {
	const order: string[] = [];
	const counts = new Map<string, number>();
	for (const style of styles) {
		const category = categoryOf(style);
		counts.set(category, (counts.get(category) || 0) + 1);
		if (!order.includes(category)) order.push(category);
	}
	return [
		{ id: 'all', label: 'All', count: styles.length },
		...order.map((category) => ({ id: category, label: category, count: counts.get(category) || 0 }))
	];
}

/** `category` of `'all'` matches every style; `text` matches name, description
 *  or category, case-insensitively. Both filters AND together. */
export function filterStyles(styles: readonly PresetStyle[], category: string, text: string): PresetStyle[] {
	const needle = text.trim().toLowerCase();
	return styles.filter((style) => {
		if (category !== 'all' && categoryOf(style) !== category) return false;
		if (!needle) return true;
		return (
			style.name.toLowerCase().includes(needle) ||
			(style.description || '').toLowerCase().includes(needle) ||
			categoryOf(style).toLowerCase().includes(needle)
		);
	});
}
