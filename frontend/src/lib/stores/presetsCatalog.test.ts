import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const listPresets = vi.fn();
vi.mock('$lib/services/api/index', () => ({
	api: { listPresets }
}));

async function loadModule() {
	vi.resetModules();
	return import('./presetsCatalog');
}

describe('presetsCatalog', () => {
	beforeEach(() => {
		listPresets.mockReset();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('reuses the in-flight request for overlapping callers instead of firing a second one', async () => {
		const { loadPresets } = await loadModule();
		let resolveFetch!: (value: unknown) => void;
		listPresets.mockReturnValueOnce(new Promise((r) => (resolveFetch = r)));

		const first = loadPresets();
		const second = loadPresets();
		resolveFetch({ success: true, data: [{ id: 'a' }] });

		await expect(first).resolves.toEqual({ success: true, data: [{ id: 'a' }] });
		expect(second).toBe(first);
		expect(listPresets).toHaveBeenCalledTimes(1);
	});

	it('reuses the resolved catalogue on a later call instead of refetching', async () => {
		const { loadPresets } = await loadModule();
		listPresets.mockResolvedValueOnce({ success: true, data: [{ id: 'a' }] });

		await loadPresets();
		await loadPresets();

		expect(listPresets).toHaveBeenCalledTimes(1);
	});

	it('refetches when force is set even though a cached catalogue exists', async () => {
		const { loadPresets } = await loadModule();
		listPresets.mockResolvedValue({ success: true, data: [{ id: 'a' }] });

		await loadPresets();
		await loadPresets({ force: true });

		expect(listPresets).toHaveBeenCalledTimes(2);
	});

	it('refetches after invalidatePresets() instead of reusing the stale catalogue', async () => {
		const { loadPresets, invalidatePresets } = await loadModule();
		listPresets.mockResolvedValue({ success: true, data: [{ id: 'a' }] });

		await loadPresets();
		invalidatePresets();
		await loadPresets();

		expect(listPresets).toHaveBeenCalledTimes(2);
	});

	it('clears the cache on a failed fetch so the next call retries instead of replaying the rejection', async () => {
		const { loadPresets } = await loadModule();
		listPresets.mockRejectedValueOnce(new Error('network error'));
		listPresets.mockResolvedValueOnce({ success: true, data: [{ id: 'a' }] });

		await expect(loadPresets()).rejects.toThrow('network error');
		await expect(loadPresets()).resolves.toEqual({ success: true, data: [{ id: 'a' }] });
		expect(listPresets).toHaveBeenCalledTimes(2);
	});

	it('reuses the cached catalogue while still inside the freshness window', async () => {
		vi.useFakeTimers();
		vi.setSystemTime(0);
		const { loadPresets } = await loadModule();
		listPresets.mockResolvedValue({ success: true, data: [{ id: 'a' }] });

		await loadPresets();
		vi.setSystemTime(59_000);
		await loadPresets();

		expect(listPresets).toHaveBeenCalledTimes(1);
	});

	it('refetches once the cached catalogue is older than the freshness window', async () => {
		vi.useFakeTimers();
		vi.setSystemTime(0);
		const { loadPresets } = await loadModule();
		listPresets.mockResolvedValue({ success: true, data: [{ id: 'a' }] });

		await loadPresets();
		vi.setSystemTime(60_000);
		await loadPresets();

		expect(listPresets).toHaveBeenCalledTimes(2);
	});
});
