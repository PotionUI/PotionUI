import { describe, expect, it, vi } from 'vitest';
import { cacheFirst, fetchAndCache, navigateOffline, precacheShell } from './runtime';

function makeFakeCache() {
	const store = new Map<string, Response>();
	const keyFor = (req: RequestInfo) => (typeof req === 'string' ? req : req.url);
	return {
		store,
		match: vi.fn(async (req: RequestInfo) => store.get(keyFor(req))),
		put: vi.fn(async (req: RequestInfo, res: Response) => {
			store.set(keyFor(req), res);
		})
	} as unknown as Cache & { store: Map<string, Response> };
}

describe('fetchAndCache', () => {
	it('caches the response on a genuine ok status', async () => {
		const cache = makeFakeCache();
		const fetchImpl = vi.fn().mockResolvedValue(new Response('body', { status: 200 }));

		const response = await fetchAndCache('/asset.js', cache, fetchImpl);

		expect(await response.text()).toBe('body');
		expect(cache.put).toHaveBeenCalledTimes(1);
		expect(cache.store.has('/asset.js')).toBe(true);
	});

	it('does not cache a non-ok response', async () => {
		const cache = makeFakeCache();
		const fetchImpl = vi.fn().mockResolvedValue(new Response('nope', { status: 500 }));

		await fetchAndCache('/asset.js', cache, fetchImpl);

		expect(cache.put).not.toHaveBeenCalled();
		expect(cache.store.size).toBe(0);
	});
});

describe('cacheFirst', () => {
	it('serves a cache hit without calling fetch', async () => {
		const cache = makeFakeCache();
		cache.store.set('/_app/immutable/chunks/a.js', new Response('cached', { status: 200 }));
		const fetchImpl = vi.fn();
		const request = { url: '/_app/immutable/chunks/a.js' } as unknown as Request;

		const response = await cacheFirst(request, cache, fetchImpl);

		expect(await response.text()).toBe('cached');
		expect(fetchImpl).not.toHaveBeenCalled();
	});

	it('on a miss, fetches and populates the cache when the response is ok', async () => {
		const cache = makeFakeCache();
		const fetchImpl = vi.fn().mockResolvedValue(new Response('fresh', { status: 200 }));
		const request = { url: '/_app/immutable/chunks/b.js' } as unknown as Request;

		const response = await cacheFirst(request, cache, fetchImpl);

		expect(await response.text()).toBe('fresh');
		expect(cache.store.has('/_app/immutable/chunks/b.js')).toBe(true);
	});

	it('on a miss with a 500, leaves the cache empty', async () => {
		const cache = makeFakeCache();
		const fetchImpl = vi.fn().mockResolvedValue(new Response('error', { status: 500 }));
		const request = { url: '/_app/immutable/chunks/c.js' } as unknown as Request;

		await cacheFirst(request, cache, fetchImpl);

		expect(cache.store.size).toBe(0);
	});
});

describe('navigateOffline', () => {
	it('serves the cached shell', async () => {
		const cache = makeFakeCache();
		cache.store.set('/', new Response('<html>shell</html>', { status: 200 }));

		const response = await navigateOffline(cache, '/');

		expect(await response.text()).toBe('<html>shell</html>');
	});

	it('falls back to a 503 when the shell was never cached', async () => {
		const cache = makeFakeCache();

		const response = await navigateOffline(cache, '/');

		expect(response.status).toBe(503);
	});
});

describe('precacheShell', () => {
	it('caches the shell HTML and the /_app/ assets it references, plus eligible static files', async () => {
		const cache = makeFakeCache();
		const html = `<link href="/_app/immutable/entry/start.js" rel="modulepreload">`;
		const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
			const url = typeof input === 'string' ? input : input.toString();
			if (url === '/') return new Response(html, { status: 200 });
			return new Response('asset', { status: 200 });
		});

		await precacheShell(
			cache,
			['/favicon.png', '/frontend-kit/hero.png', '/brand/logo.png~'],
			'/',
			fetchImpl
		);

		expect(cache.store.has('/')).toBe(true);
		expect(cache.store.has('/_app/immutable/entry/start.js')).toBe(true);
		expect(cache.store.has('/favicon.png')).toBe(true);
		expect(cache.store.has('/frontend-kit/hero.png')).toBe(false);
		expect(cache.store.has('/brand/logo.png~')).toBe(false);
	});

	it('does not throw when every fetch fails (offline install)', async () => {
		const cache = makeFakeCache();
		const fetchImpl = vi.fn().mockRejectedValue(new Error('offline'));

		await expect(precacheShell(cache, ['/favicon.png'], '/', fetchImpl)).resolves.toBeUndefined();
		expect(cache.store.size).toBe(0);
	});

	it('does not fail the whole precache when one asset 404s', async () => {
		const cache = makeFakeCache();
		const html = `<link href="/_app/immutable/entry/missing.js" rel="modulepreload">`;
		const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
			const url = typeof input === 'string' ? input : input.toString();
			if (url === '/') return new Response(html, { status: 200 });
			return new Response('not found', { status: 404 });
		});

		await expect(precacheShell(cache, [], '/', fetchImpl)).resolves.toBeUndefined();
		expect(cache.store.has('/_app/immutable/entry/missing.js')).toBe(false);
	});
});
