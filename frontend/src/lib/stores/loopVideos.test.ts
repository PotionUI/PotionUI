// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';

async function load() {
	vi.resetModules();
	return (await import('./loopVideos')).loopVideos;
}

describe('loopVideos', () => {
	beforeEach(() => {
		localStorage.clear();
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it('defaults to on', async () => {
		const store = await load();
		expect(get(store)).toBe(true);
	});

	it('persists the choice for the next load', async () => {
		const store = await load();
		store.toggle();
		expect(get(store)).toBe(false);
		const reloaded = await load();
		expect(get(reloaded)).toBe(false);
		reloaded.toggle();
		expect(get(await load())).toBe(true);
	});

	it('keeps working when storage throws', async () => {
		vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
			throw new Error('blocked');
		});
		vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
			throw new Error('blocked');
		});
		const store = await load();
		expect(get(store)).toBe(true);
		expect(() => store.toggle()).not.toThrow();
		expect(get(store)).toBe(false);
	});
});
