// Plugin catalogue category metadata.
// Keeps the display order, labels, descriptions, and icons for the categorised
// plugin catalogue (see admin/components/PluginsTab.svelte).

export type PluginCategoryId =
	| 'backends'
	| 'sources'
	| 'steps'
	| 'tools'
	| 'security'
	| 'monitoring'
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
		id: 'backends',
		label: 'Backends & compute',
		description: 'Engines, cloud providers and compute hosts that run generations',
		icon: 'bolt'
	},
	{
		id: 'sources',
		label: 'Model sources',
		description: 'Marketplaces and hubs to browse and download models from',
		icon: 'model'
	},
	{
		id: 'steps',
		label: 'Generation steps',
		description: 'Processing steps that run inside a generation',
		icon: 'layers'
	},
	{
		id: 'tools',
		label: 'Tools & pages',
		description: 'Editors, exporters and viewers you work with',
		icon: 'image'
	},
	{
		id: 'security',
		label: 'Sign-in & security',
		description: 'Login providers and access control',
		icon: 'shield'
	},
	{
		id: 'monitoring',
		label: 'Monitoring',
		description: 'Resource monitors and cleanup helpers',
		icon: 'sliders'
	},
	{
		id: 'developer',
		label: 'Developer',
		description: 'Reference implementations and extension examples',
		icon: 'code'
	},
	{
		id: 'other',
		label: 'Other',
		description: 'Plugins that fit no other group',
		icon: 'folder'
	}
];

const otherCategory = pluginCategories[pluginCategories.length - 1];

/** Resolve an (unknown/missing) category id to its metadata, defaulting to "other". */
export function resolveCategory(id: string | undefined | null): PluginCategoryMeta {
	if (!id) return otherCategory;
	return pluginCategories.find((c) => c.id === id) ?? otherCategory;
}

const pluginTypeLabels: Record<string, string> = {
	'full-stack': 'Server and interface parts',
	'backend-only': 'Server part only',
	'frontend-only': 'Interface part only'
};

export function pluginTypeLabel(type: string | undefined | null): string {
	if (!type) return 'Unknown';
	return pluginTypeLabels[type] ?? type;
}
