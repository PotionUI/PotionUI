import { describe, it, expect, vi } from 'vitest';
import { CapabilityCache, createCapabilityTracker, FAILED_FETCH_RETRY_MS } from './capabilityTracker';
import type { CloudCapabilities } from './capabilityBinder';

function caps(id: string): CloudCapabilities {
	return { model_id: id, params: [], inputs: [] };
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('capability tracker', () => {
	it('fetches once per model id and keeps the answer in the cache', async () => {
		const cache = new CapabilityCache();
		const fetch = vi.fn(async (id: string) => caps(id));
		const loaded: string[] = [];
		const tracker = createCapabilityTracker({ fetch, cache, onLoaded: (id) => loaded.push(id) });

		tracker.select('model', 'a');
		await flush();
		tracker.select('model', 'b');
		await flush();
		tracker.select('model', 'a');
		await flush();

		expect(fetch).toHaveBeenCalledTimes(2);
		expect(loaded).toEqual(['a', 'b']);
		expect(cache.keys()).toEqual(['a', 'b']);
	});

	it('does not fetch again for a model that is already cached', async () => {
		const cache = new CapabilityCache();
		cache.set('a', caps('a'));
		const fetch = vi.fn(async (id: string) => caps(id));
		const tracker = createCapabilityTracker({ fetch, cache, onLoaded: vi.fn() });
		tracker.select('model', 'a');
		await flush();
		expect(fetch).not.toHaveBeenCalled();
	});

	it('shares one request between fields that select the same model', async () => {
		const fetch = vi.fn(async (id: string) => caps(id));
		const tracker = createCapabilityTracker({ fetch, onLoaded: vi.fn() });
		tracker.select('one', 'a');
		tracker.select('two', 'a');
		await flush();
		expect(fetch).toHaveBeenCalledTimes(1);
	});

	it('still caches the answer of a model that is no longer selected', async () => {
		const slow = deferred<CloudCapabilities>();
		const cache = new CapabilityCache();
		const fetch = vi.fn((id: string) => (id === 'a' ? slow.promise : Promise.resolve(caps(id))));
		const tracker = createCapabilityTracker({ fetch, cache, onLoaded: vi.fn() });
		tracker.select('model', 'a');
		tracker.select('model', 'b');
		slow.resolve(caps('a'));
		await flush();
		expect(cache.has('a')).toBe(true);
	});

	it('retries a failed model after the pause, not before', async () => {
		let clock = 0;
		const fetch = vi
			.fn<(id: string) => Promise<CloudCapabilities>>()
			.mockRejectedValueOnce(new Error('404'))
			.mockResolvedValue(caps('a'));
		const loaded: string[] = [];
		const tracker = createCapabilityTracker({ fetch, onLoaded: (id) => loaded.push(id), now: () => clock });

		tracker.select('model', 'a');
		await flush();
		expect(loaded).toEqual([]);

		tracker.select('model', 'a');
		await flush();
		expect(fetch).toHaveBeenCalledTimes(1);

		clock += FAILED_FETCH_RETRY_MS + 1;
		tracker.select('model', 'a');
		await flush();
		expect(fetch).toHaveBeenCalledTimes(2);
		expect(loaded).toEqual(['a']);
	});

	it('does not fetch for a cleared model', async () => {
		const fetch = vi.fn(async (id: string) => caps(id));
		const tracker = createCapabilityTracker({ fetch, onLoaded: vi.fn() });
		tracker.select('model', null);
		await flush();
		expect(fetch).not.toHaveBeenCalled();
	});

	it('stops reporting after destroy', async () => {
		const slow = deferred<CloudCapabilities>();
		const onLoaded = vi.fn();
		const tracker = createCapabilityTracker({ fetch: () => slow.promise, onLoaded });
		tracker.select('model', 'a');
		tracker.destroy();
		slow.resolve(caps('a'));
		await flush();
		expect(onLoaded).not.toHaveBeenCalled();
	});

	it('asks again once an answer is older than the cache lifetime, and keeps the old one meanwhile', async () => {
		let clock = 0;
		const cache = new CapabilityCache(1000, () => clock);
		const slow = deferred<CloudCapabilities>();
		const fetch = vi
			.fn<(id: string) => Promise<CloudCapabilities>>()
			.mockResolvedValueOnce(caps('a'))
			.mockReturnValueOnce(slow.promise);
		const loaded: string[] = [];
		const tracker = createCapabilityTracker({ fetch, cache, onLoaded: (id) => loaded.push(id) });

		tracker.select('model', 'a');
		await flush();
		clock = 500;
		tracker.select('model', 'a');
		await flush();
		expect(fetch).toHaveBeenCalledTimes(1);

		clock = 1500;
		tracker.select('model', 'a');
		await flush();
		expect(fetch).toHaveBeenCalledTimes(2);
		expect(cache.get('a')).toEqual(caps('a'));

		slow.resolve({ ...caps('a'), label: 'fresh' });
		await flush();
		expect(cache.get('a')?.label).toBe('fresh');
		expect(loaded).toEqual(['a', 'a']);
	});
});
