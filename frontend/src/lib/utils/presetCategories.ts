export type PresetCategoryId = 'image' | 'video' | 'audio' | '3d' | 'utility';

export interface PresetCategoryMeta {
	id: PresetCategoryId;
	label: string;
	icon: string;
}

export const PRESET_CATEGORIES: readonly PresetCategoryMeta[] = [
	{ id: 'image', label: 'Image', icon: 'photo' },
	{ id: 'video', label: 'Video', icon: 'film' },
	{ id: 'audio', label: 'Audio', icon: 'audio' },
	{ id: '3d', label: '3D', icon: 'cube' },
	{ id: 'utility', label: 'Utility', icon: 'wand' }
];

const categoryById = new Map(PRESET_CATEGORIES.map((category) => [category.id, category]));

export function presetCategoryLabel(category: string | null | undefined): string {
	if (!category) return 'Uncategorized';
	const known = categoryById.get(category as PresetCategoryId);
	if (known) return known.label;
	return category.charAt(0).toUpperCase() + category.slice(1);
}

export function presetCategoryIcon(category: string | null | undefined): string {
	if (!category) return 'layers';
	return categoryById.get(category as PresetCategoryId)?.icon ?? 'layers';
}

export function presetCategoryOrder(category: string | null | undefined): number {
	if (!category) return PRESET_CATEGORIES.length;
	const index = PRESET_CATEGORIES.findIndex((entry) => entry.id === category);
	return index === -1 ? PRESET_CATEGORIES.length : index;
}
