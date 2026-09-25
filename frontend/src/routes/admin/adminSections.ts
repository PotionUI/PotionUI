export interface AdminSection {
	key: string;
	label: string;
	icon: string;
}

export const ADMIN_SECTIONS: readonly AdminSection[] = [
	{ key: 'settings', label: 'System Settings', icon: 'settings' },
	{ key: 'models', label: 'Models', icon: 'database' },
	{ key: 'presets', label: 'Presets', icon: 'cube' },
	{ key: 'recipes', label: 'Recipes', icon: 'list' },
	{ key: 'backends', label: 'Backends', icon: 'server' },
	{ key: 'generations', label: 'Generations', icon: 'generation' },
	{ key: 'users', label: 'Users', icon: 'group' },
	{ key: 'llm', label: 'LLM / Assistant', icon: 'model' },
	{ key: 'plugins', label: 'Plugins', icon: 'extension' },
	{ key: 'downloads', label: 'Downloads', icon: 'download' },
	{ key: 'automations', label: 'Automations', icon: 'bolt' },
	{ key: 'docs', label: 'Documentation', icon: 'document' },
	{ key: 'stats', label: 'Stats', icon: 'gauge' }
];

export const ADMIN_PLUGIN_TAB_FALLBACK_ICON = 'box';

export function adminSectionIcon(key: string): string {
	return ADMIN_SECTIONS.find((section) => section.key === key)?.icon ?? ADMIN_PLUGIN_TAB_FALLBACK_ICON;
}
