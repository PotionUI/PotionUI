import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/services/api', () => ({
	api: { getModelsLocation: vi.fn(), applyModelsLocation: vi.fn() }
}));

import { api } from '$lib/services/api';
import { ModelsLocationState } from './state.svelte';

function config(overrides: Record<string, unknown> = {}) {
	return {
		external_path: null,
		overrides: {},
		directories: [],
		windows_unsupported: false,
		...overrides
	};
}

describe('ModelsLocationState', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('load() populates config on success', async () => {
		vi.mocked(api.getModelsLocation).mockResolvedValue({ success: true, data: config() });

		const state = new ModelsLocationState();
		await state.load();

		expect(state.config).toEqual(config());
		expect(state.loading).toBe(false);
		expect(state.error).toBeNull();
	});

	it('load() sets error on a failed response instead of throwing', async () => {
		vi.mocked(api.getModelsLocation).mockResolvedValue({ success: false, message: 'nope' });

		const state = new ModelsLocationState();
		await state.load();

		expect(state.config).toBeNull();
		expect(state.error).toBe('nope');
	});

	it('apply() sends the path and overrides, updates config, and returns true on success', async () => {
		const applied = config({ external_path: '/data/models' });
		vi.mocked(api.applyModelsLocation).mockResolvedValue({ success: true, data: applied });

		const state = new ModelsLocationState();
		const ok = await state.apply('/data/models', { loras: '/data/loras' });

		expect(api.applyModelsLocation).toHaveBeenCalledWith('/data/models', { loras: '/data/loras' });
		expect(ok).toBe(true);
		expect(state.config).toEqual(applied);
		expect(state.applying).toBe(false);
	});

	it('apply() surfaces the error and returns false without touching config on failure', async () => {
		vi.mocked(api.applyModelsLocation).mockResolvedValue({ success: false, message: 'refused' });

		const state = new ModelsLocationState();
		const ok = await state.apply('/data/models');

		expect(ok).toBe(false);
		expect(state.error).toBe('refused');
		expect(state.config).toBeNull();
	});
});
