/**
 * Loads the plugin-contributed media tools once per page load and registers
 * them onto the shared registry. Kept out of `tools.ts` so the registry itself
 * stays a pure, testable module.
 */
import { writable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import { logger, getErrorMessage } from '$lib/utils/logger';
import {
	pluginMediaToolFromManifest,
	registerMediaTool,
	unregisterMediaTools,
	type PluginMediaToolEntry
} from './tools';

/** Ticks once per applied snapshot, so a mounted menu can rebuild its groups. */
export const mediaToolRegistrations = writable(0);

let loading: Promise<void> | null = null;

async function load(): Promise<void> {
	const response = await api.getHistoryTools();
	if (!response.success) throw new Error(response.error || 'Request was unsuccessful');
	const entries = (response.data ?? []) as PluginMediaToolEntry[];

	unregisterMediaTools((tool) => tool.source === 'plugin');
	for (const entry of entries) registerMediaTool(pluginMediaToolFromManifest(entry));
	mediaToolRegistrations.update((tick) => tick + 1);
}

/**
 * Never throws: the core tools work without the plugin catalog, and a failed
 * attempt is not remembered so the next caller retries.
 */
export function loadPluginMediaTools(): Promise<void> {
	if (!loading) {
		loading = load()
			.catch((error) => {
				logger.error('Failed to load plugin media tools:', getErrorMessage(error));
				loading = null;
			})
			.then(() => undefined);
	}
	return loading;
}
