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
		try {
			await cache.put(request, response.clone());
		} catch {
			// cache.put can fail on its own (e.g. QuotaExceededError); the
			// network response is still good and must reach the caller
		}
	}
	return response;
}

/**
 * Fetches a required asset and caches it, rejecting on a network failure or a
 * non-ok status. Used for the shell and its boot chunks during install: a
 * missing/broken boot asset must fail the install rather than silently ship
 * a shell that can't start.
 */
async function fetchAndCacheOrThrow(url: string, cache: Cache, fetchImpl: Fetcher): Promise<void> {
	const response = await fetchImpl(url);
	if (!response.ok) {
		throw new Error(`Service worker install: required asset ${url} responded with ${response.status}`);
	}
	await cache.put(url, response);
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
 * Install-time precache: the SPA shell (`shellUrl`) plus the `/_app/` boot
 * assets it references are a hard precondition - a failed or non-ok fetch
 * for any of them rejects, which fails the install event so the browser
 * keeps the previous worker and cache instead of activating an incomplete
 * one. The small static allowlist (fonts/icons/brand/favicon/manifest) is
 * best-effort on top of that: a miss there is filled in at runtime.
 */
export async function precacheShell(
	cache: Cache,
	staticFiles: readonly string[],
	shellUrl: string,
	fetchImpl: Fetcher
): Promise<void> {
	const shellResponse = await fetchImpl(shellUrl);
	if (!shellResponse.ok) {
		throw new Error(`Service worker install: shell fetch for ${shellUrl} responded with ${shellResponse.status}`);
	}
	const html = await shellResponse.clone().text();
	await cache.put(shellUrl, shellResponse);

	const bootAssets = extractShellAssets(html);
	await Promise.all(bootAssets.map((url) => fetchAndCacheOrThrow(url, cache, fetchImpl)));

	const staticAssets = selectPrecacheFiles(staticFiles);
	await Promise.all(
		staticAssets.map((url) =>
			fetchAndCache(url, cache, fetchImpl).catch(() => {
				// best-effort precache; a miss here is filled in at runtime
			})
		)
	);
}
