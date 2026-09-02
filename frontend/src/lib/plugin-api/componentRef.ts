export interface PluginComponentRef {
	pluginId: string;
	asset: string;
}

/** "plugin:<id>:<asset>" -> {pluginId, asset}, or null if malformed. */
export function parseComponentRef(ref: string | null | undefined): PluginComponentRef | null {
	if (!ref) return null;
	const parts = ref.split(':');
	if (parts[0] !== 'plugin' || parts.length < 3 || !parts[1] || !parts.slice(2).join(':')) return null;
	return { pluginId: parts[1], asset: parts.slice(2).join(':') };
}
