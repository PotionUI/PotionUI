import { logger } from '$lib/utils/logger';
import { api } from '$lib/services/api';
import type { ModelsLocationConfig } from '$lib/services/api/models';

/** One editable string per model directory - an unset override is '' so the
 * inputs bound to these never see undefined. */
export function overrideDraftsFor(config: ModelsLocationConfig | null): Record<string, string> {
	if (!config) return {};
	return Object.fromEntries(
		config.directories.map((dir) => [dir.directory, config.overrides?.[dir.directory] ?? ''])
	);
}

// Shared load/apply logic for GET /api/models/location and POST
// /api/models/location/apply, used by the admin System Settings panel
// (frontend/src/routes/admin/components/settings/ModelsLocationPanel.svelte)
// and the setup wizard's models-location step.
export class ModelsLocationState {
	config = $state<ModelsLocationConfig | null>(null);
	loading = $state(true);
	applying = $state(false);
	error = $state<string | null>(null);

	async load(): Promise<void> {
		this.loading = true;
		this.error = null;
		try {
			const response = await api.getModelsLocation();
			if (response.success && response.data) {
				this.config = response.data;
			} else {
				this.error = response.message ?? 'Failed to load the models location.';
			}
		} catch (e) {
			logger.error('Failed to load models location:', e);
			this.error = 'Failed to load the models location.';
		} finally {
			this.loading = false;
		}
	}

	async apply(externalPath: string, overrides?: Record<string, string>): Promise<boolean> {
		this.applying = true;
		this.error = null;
		try {
			const response = await api.applyModelsLocation(externalPath, overrides);
			if (response.success && response.data) {
				this.config = response.data;
				return true;
			}
			this.error = response.message ?? 'Failed to apply the models location.';
			return false;
		} catch (e) {
			logger.error('Failed to apply models location:', e);
			this.error = 'Failed to apply the models location.';
			return false;
		} finally {
			this.applying = false;
		}
	}
}
