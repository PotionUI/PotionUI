/**
 * Fetches the `/api/fields/types` manifest (A3's `FieldTypeRegistry`) once at
 * app init and registers every plugin-sourced entry into the frontend field
 * registry. Core-sourced entries (`source: "core"`) are skipped - they are
 * already registered statically by `fields/builtin.ts`, which owns the
 * canonical alias table.
 */
import { logger } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';
import { registerFieldComponent } from '$lib/fields/registry';

let initialized = false;

export async function initFieldTypes(): Promise<void> {
	if (initialized) return;
	initialized = true;

	try {
		const response = await api.getClient().get('/api/fields/types');
		const data = response.data;

		if (!data?.success) {
			logger.warn('Failed to load field types manifest:', data?.message);
			return;
		}

		const manifest: Array<{ type: string; component: string; source: string }> =
			data.data || [];

		for (const entry of manifest) {
			if (entry.source === 'core' || !entry.component) continue;

			// "plugin:<id>:<Asset.js>" - see src/core/plugins/registry.py.
			const parts = entry.component.split(':');
			if (parts[0] !== 'plugin' || parts.length < 3) continue;

			const pluginId = parts[1];
			const asset = parts.slice(2).join(':');
			registerFieldComponent(entry.type, { pluginId, asset });
		}
	} catch (err) {
		// Non-fatal: core field types still work via the static registry.
		logger.error('Failed to initialize field types:', err);
	}
}
