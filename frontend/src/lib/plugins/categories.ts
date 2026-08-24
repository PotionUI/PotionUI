// Plugin catalogue category metadata.
// Keeps the display order, labels, descriptions, and icons for the categorised
// plugin catalogue (see admin/components/PluginsTab.svelte).

export type PluginCategoryId =
	| 'generation'
	| 'models'
	| 'system'
	| 'media'
	| 'workflow'
	| 'developer'
	| 'other';

export interface PluginCategoryMeta {
	id: PluginCategoryId;
	label: string;
	description: string;
	/** Icon name, resolved via `$lib/utils/IconLibrary` (see `Icon.svelte`). */
	icon: string;
}

export const pluginCategories: PluginCategoryMeta[] = [
	{
		id: 'generation',
		label: 'Generation & Backends',
		description: 'Engines and remote services that produce images and video',
		icon: 'bolt'
	},
	{
		id: 'models',
		label: 'Models & Assets',
		description: 'Downloading, browsing, and organising models and datasets',
		icon: 'model'
	},
	{
		id: 'system',
		label: 'System & Performance',
		description: 'GPU memory, monitoring, and resource management',
		icon: 'sliders'
	},
	{
		id: 'media',
		label: 'Media & Editing',
		description: 'Viewing and editing generated media',
		icon: 'image'
	},
	{
		id: 'workflow',
		label: 'Workflow & Authoring',
		description: 'Building presets, forms, and pipelines',
		icon: 'layers'
	},
	{
		id: 'developer',
		label: 'Developer & Examples',
		description: 'Reference implementations and extension examples',
		icon: 'code'
	},
	{
		id: 'other',
		label: 'Other',
		description: 'Uncategorised plugins',
		icon: 'folder'
	}
];

const otherCategory = pluginCategories[pluginCategories.length - 1];

/** Resolve an (unknown/missing) category id to its metadata, defaulting to "other". */
export function resolveCategory(id: string | undefined | null): PluginCategoryMeta {
	if (!id) return otherCategory;
	return pluginCategories.find((c) => c.id === id) ?? otherCategory;
}
