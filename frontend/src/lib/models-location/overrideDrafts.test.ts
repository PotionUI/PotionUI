import { describe, expect, it } from 'vitest';
import { overrideDraftsFor } from './state.svelte';
import type { ModelsLocationConfig } from '$lib/services/api/models';

const config = {
	directories: [{ directory: 'checkpoints' }, { directory: 'loras' }, { directory: 'vae' }],
	overrides: { loras: '/mnt/shared/loras' }
} as unknown as ModelsLocationConfig;

describe('overrideDraftsFor', () => {
	it('yields a string for every directory, empty when there is no override', () => {
		expect(overrideDraftsFor(config)).toEqual({ checkpoints: '', loras: '/mnt/shared/loras', vae: '' });
	});

	it('is empty without a config', () => {
		expect(overrideDraftsFor(null)).toEqual({});
	});
});
