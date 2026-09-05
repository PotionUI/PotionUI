import { extractShellAssets, selectPrecacheFiles } from './shell';

type Fetcher = (input: RequestInfo | URL) => Promise<Response>;

/** Fetches a request and caches the response only on a genuine ok status. */
export async function fetchAndCache(
	request: RequestInfo,
	cache: Cache,
	fetchImpl: Fetcher
): Promise<Response> {
	const response = await fetchImpl(request);
	if (response.ok) {
		await cache.put(request, response.clone());
	}
	return response;
}

/** Cache-first strategy for content-hashed and precache-eligible assets. */
export async function cacheFirst(
	request: Request,
	cache: Cache,
	fetchImpl: Fetcher
): Promise<Response> {
	const cached = await cache.match(request);
	if (cached) return cached;
	return fetchAndCache(request, cache, fetchImpl);
}

/** Offline navigation fallback: the cached SPA shell, or a 503 if it was never cached. */
export async function navigateOffline(cache: Cache, shellUrl: string): Promise<Response> {
	const cached = await cache.match(shellUrl);
	return cached ?? new Response('Offline', { status: 503 });
}

/**
 * Install-time precache: the SPA shell (`shellUrl`) plus the `/_app/` assets
 * it references, plus the small set of always-useful static files - not the
 * full build output. A failed fetch (offline install, a since-removed asset)
 * is swallowed per-URL; the fetch handler fills any gap in on first request.
 */
export async function precacheShell(
	cache: Cache,
	staticFiles: readonly string[],
	shellUrl: string,
	fetchImpl: Fetcher
): Promise<void> {
	let html = '';
	try {
		const shellResponse = await fetchImpl(shellUrl);
		if (shellResponse.ok) {
			html = await shellResponse.clone().text();
			await cache.put(shellUrl, shellResponse);
		}
	} catch {
		// offline install: picked up by the navigation fallback once online
	}

	const urls = [...extractShellAssets(html), ...selectPrecacheFiles(staticFiles)];
	await Promise.all(
		urls.map((url) =>
			fetchAndCache(url, cache, fetchImpl).catch(() => {
				// best-effort precache; a miss here is filled in at runtime
			})
		)
	);
}
