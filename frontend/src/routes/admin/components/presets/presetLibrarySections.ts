import type { LibrarySectionMeta } from '$lib/components/library/librarySection';
import { PRESET_CATEGORIES, type PresetCategoryId } from '$lib/utils/presetCategories';

export type PresetLibrarySection = 'all' | PresetCategoryId;

export const PRESET_LIBRARY_SECTIONS: readonly LibrarySectionMeta<PresetLibrarySection>[] = [
	{ id: 'all', label: 'All presets', icon: 'grid' },
	...PRESET_CATEGORIES.map((category) => ({ id: category.id, label: category.label, icon: category.icon }))
];
