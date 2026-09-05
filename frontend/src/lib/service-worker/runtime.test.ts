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

function makeFakeCacheStorage(initialCaches: Record<string, ReturnType<typeof makeFakeCache>> = {}) {
	const store = new Map(Object.entries(initialCaches));
	return {
		store,
		open: vi.fn(async (name: string) => {
			if (!store.has(name)) store.set(name, makeFakeCache());
			return store.get(name)!;
		}),
		keys: vi.fn(async () => [...store.keys()]),
		delete: vi.fn(async (name: string) => store.delete(name))
	};
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

	it('still returns the network response when cache.put throws (e.g. QuotaExceededError)', async () => {
		const cache = makeFakeCache();
		(cache.put as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
			new DOMException('quota exceeded', 'QuotaExceededError')
		);
		const fetchImpl = vi.fn().mockResolvedValue(new Response('body', { status: 200 }));

		const response = await fetchAndCache('/asset.js', cache, fetchImpl);

		expect(response.status).toBe(200);
		expect(await response.text()).toBe('body');
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

	it('rejects when the shell fetch fails outright (offline install)', async () => {
		const cache = makeFakeCache();
		const fetchImpl = vi.fn().mockRejectedValue(new Error('offline'));

		await expect(precacheShell(cache, ['/favicon.png'], '/', fetchImpl)).rejects.toThrow('offline');
		expect(cache.store.size).toBe(0);
	});

	it('rejects when the shell responds non-ok', async () => {
		const cache = makeFakeCache();
		const fetchImpl = vi.fn().mockResolvedValue(new Response('server error', { status: 500 }));

		await expect(precacheShell(cache, [], '/', fetchImpl)).rejects.toThrow();
		expect(cache.store.size).toBe(0);
	});

	it('rejects when a boot asset extracted from the shell 500s', async () => {
		const cache = makeFakeCache();
		const html = `<link href="/_app/immutable/entry/broken.js" rel="modulepreload">`;
		const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
			const url = typeof input === 'string' ? input : input.toString();
			if (url === '/') return new Response(html, { status: 200 });
			return new Response('server error', { status: 500 });
		});

		await expect(precacheShell(cache, [], '/', fetchImpl)).rejects.toThrow();
		// the shell itself was cached before the boot asset failed; the caller
		// (install handler) discards this cache instance on rejection anyway
		expect(cache.store.has('/_app/immutable/entry/broken.js')).toBe(false);
	});

	it('still resolves when only a best-effort static asset fails', async () => {
		const cache = makeFakeCache();
		const html = `<link href="/_app/immutable/entry/start.js" rel="modulepreload">`;
		const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
			const url = typeof input === 'string' ? input : input.toString();
			if (url === '/favicon.png') return Promise.reject(new Error('static asset offline'));
			if (url === '/') return new Response(html, { status: 200 });
			return new Response('asset', { status: 200 });
		});

		await expect(precacheShell(cache, ['/favicon.png'], '/', fetchImpl)).resolves.toBeUndefined();
		expect(cache.store.has('/')).toBe(true);
		expect(cache.store.has('/_app/immutable/entry/start.js')).toBe(true);
		expect(cache.store.has('/favicon.png')).toBe(false);
	});
});

describe('install/activate lifecycle (modeled with the runtime helpers)', () => {
	// service-worker.ts itself can't be imported under vitest (the SvelteKit
	// vite plugin's own resolveId guard for `$service-worker` throws when the
	// module runner touches it a second time) - so the install/activate
	// sequence it wires up is reproduced here directly against fakes.

	async function runInstall(
		caches: ReturnType<typeof makeFakeCacheStorage>,
		cacheName: string,
		fetchImpl: ReturnType<typeof vi.fn>
	): Promise<void> {
		const cache = await caches.open(cacheName);
		await precacheShell(cache, [], '/', fetchImpl);
	}

	async function runActivate(
		caches: ReturnType<typeof makeFakeCacheStorage>,
		cacheName: string
	): Promise<void> {
		const keys = await caches.keys();
		await Promise.all(keys.filter((key) => key !== cacheName).map((key) => caches.delete(key)));
	}

	it('a failed required-asset install leaves the previous cache intact and never reaches activate', async () => {
		const OLD_CACHE = 'potionui-cache-v1';
		const NEW_CACHE = 'potionui-cache-v2';
		const oldCache = makeFakeCache();
		oldCache.store.set('/', new Response('<html>old shell</html>', { status: 200 }));
		const caches = makeFakeCacheStorage({ [OLD_CACHE]: oldCache });

		const html = `<link href="/_app/immutable/entry/broken.js" rel="modulepreload">`;
		const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
			const url = typeof input === 'string' ? input : input.toString();
			if (url === '/') return new Response(html, { status: 200 });
			return new Response('server error', { status: 500 });
		});

		await expect(runInstall(caches, NEW_CACHE, fetchImpl)).rejects.toThrow();
		// A rejected `install` waitUntil promise means the browser never fires
		// `activate` for this worker version, so runActivate is deliberately
		// not invoked here - the old worker and its cache stay in control.
		expect(caches.store.has(OLD_CACHE)).toBe(true);
		expect(caches.store.get(OLD_CACHE)?.store.has('/')).toBe(true);
		expect(await caches.store.get(OLD_CACHE)?.store.get('/')?.text()).toBe('<html>old shell</html>');
	});

	it('a successful install can proceed to activate and drop the old cache', async () => {
		const OLD_CACHE = 'potionui-cache-v1';
		const NEW_CACHE = 'potionui-cache-v2';
		const oldCache = makeFakeCache();
		oldCache.store.set('/', new Response('<html>old shell</html>', { status: 200 }));
		const caches = makeFakeCacheStorage({ [OLD_CACHE]: oldCache });

		const html = `<link href="/_app/immutable/entry/start.js" rel="modulepreload">`;
		const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
			const url = typeof input === 'string' ? input : input.toString();
			if (url === '/') return new Response(html, { status: 200 });
			return new Response('asset', { status: 200 });
		});

		await runInstall(caches, NEW_CACHE, fetchImpl);
		await runActivate(caches, NEW_CACHE);

		expect(caches.store.has(OLD_CACHE)).toBe(false);
		expect(caches.store.has(NEW_CACHE)).toBe(true);
	});
});
