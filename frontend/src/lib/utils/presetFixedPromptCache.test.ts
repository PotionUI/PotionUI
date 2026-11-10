import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('$lib/services/api/index', () => ({ api: { getPresetFixedPrompt: vi.fn() } }));

import { getPresetFixedPrompt, invalidatePresetFixedPromptCache } from './presetFixedPromptCache';

function httpError(status: number) {
	return { isAxiosError: true, message: `status ${status}`, response: { status, data: { success: false } } };
}

describe('getPresetFixedPrompt', () => {
	beforeEach(() => invalidatePresetFixedPromptCache());

	it('loads once per preset and normalizes the body', async () => {
		const load = vi.fn(async () => ({ success: true, data: { prefix: 'a', suffix: '', negative: '', modes: null } }));
		expect(await getPresetFixedPrompt('p1', load)).toEqual({ prefix: 'a', suffix: '', negative: '', modes: null });
		await getPresetFixedPrompt('p1', load);
		expect(load).toHaveBeenCalledTimes(1);
	});

	it('resolves to null on a 403 without throwing, and does not ask again', async () => {
		const load = vi.fn(async () => {
			throw httpError(403);
		});
		await expect(getPresetFixedPrompt('p2', load)).resolves.toBeNull();
		await getPresetFixedPrompt('p2', load);
		expect(load).toHaveBeenCalledTimes(1);
	});

	it('asks again after a network failure', async () => {
		const load = vi.fn(async () => {
			throw new Error('offline');
		});
		await expect(getPresetFixedPrompt('p3', load)).resolves.toBeNull();
		await getPresetFixedPrompt('p3', load);
		expect(load).toHaveBeenCalledTimes(2);
	});

	it('reloads a preset after it is invalidated', async () => {
		const load = vi.fn(async () => ({ success: true, data: { prefix: '', suffix: 'b', negative: '', modes: null } }));
		await getPresetFixedPrompt('p4', load);
		invalidatePresetFixedPromptCache('p4');
		await getPresetFixedPrompt('p4', load);
		expect(load).toHaveBeenCalledTimes(2);
	});

	it('is null without a preset', async () => {
		await expect(getPresetFixedPrompt(null)).resolves.toBeNull();
	});
});
