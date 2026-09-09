/**
 * Loads the plugin-contributed History tools once per page load and registers
 * them onto the shared registry. Kept out of `tools.ts` so the registry itself
 * stays a pure, testable module.
 */
import { writable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import { logger, getErrorMessage } from '$lib/utils/logger';
import {
	pluginHistoryToolFromManifest,
	registerHistoryTool,
	unregisterHistoryTools,
	type PluginHistoryToolEntry
} from './tools';

/** Ticks once per applied snapshot, so a mounted menu can rebuild its groups. */
export const historyToolRegistrations = writable(0);

let loading: Promise<void> | null = null;

async function load(): Promise<void> {
	const response = await api.getHistoryTools();
	if (!response.success) throw new Error(response.error || 'Request was unsuccessful');
	const entries = (response.data ?? []) as PluginHistoryToolEntry[];

	unregisterHistoryTools((tool) => tool.source === 'plugin');
	for (const entry of entries) registerHistoryTool(pluginHistoryToolFromManifest(entry));
	historyToolRegistrations.update((tick) => tick + 1);
}

/**
 * Never throws: the core tools work without the plugin catalog, and a failed
 * attempt is not remembered so the next caller retries.
 */
export function loadPluginHistoryTools(): Promise<void> {
	if (!loading) {
		loading = load()
			.catch((error) => {
				logger.error('Failed to load plugin history tools:', getErrorMessage(error));
				loading = null;
			})
			.then(() => undefined);
	}
	return loading;
}
