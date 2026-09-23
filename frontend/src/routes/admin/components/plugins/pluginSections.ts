import { oneOf, type LibrarySectionMeta } from '$lib/components/library/librarySection';
import { pluginCategories, type PluginCategoryId } from '$lib/plugins/categories';

export type PluginSection = 'all' | PluginCategoryId;

export const PLUGIN_SECTIONS: readonly LibrarySectionMeta<PluginSection>[] = [
	{ id: 'all', label: 'All plugins', icon: 'grid' },
	...pluginCategories.map((category) => ({ id: category.id, label: category.label, icon: category.icon }))
];

const SECTION_IDS = PLUGIN_SECTIONS.map((section) => section.id);

export function isPluginSection(value: string | null | undefined): value is PluginSection {
	return !!value && (SECTION_IDS as string[]).includes(value);
}

export function pluginSectionFromSearchParams(params: URLSearchParams): PluginSection {
	return oneOf(params.get('category'), SECTION_IDS, 'all');
}
