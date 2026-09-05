// Pure helpers for the install-time precache policy. Kept dependency-free
// (no DOM parser: service workers don't have one) so they can be unit-tested
// outside the worker global scope.

const STATIC_PRECACHE_PREFIXES = ['/fonts/', '/icons/', '/brand/'];

/** Static files worth precaching: fonts, icons, brand assets, favicon, manifest. */
export function isPrecacheEligibleStaticPath(pathname: string): boolean {
	if (pathname.endsWith('~')) return false;
	if (pathname.startsWith('/frontend-kit/')) return false;
	if (pathname === '/manifest.json') return true;
	if (/^\/favicon\.[^/]+$/.test(pathname)) return true;
	return STATIC_PRECACHE_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

/** Filters the `$service-worker` `files` list down to the precache-eligible subset. */
export function selectPrecacheFiles(files: readonly string[]): string[] {
	return files.filter(isPrecacheEligibleStaticPath);
}

const LINK_TAG_RE = /<link\b([^>]*)>/gi;
const SCRIPT_TAG_RE = /<script\b([^>]*)>/gi;
const IMPORT_CALL_RE = /import\(\s*["']([^"']+)["']\s*\)/g;
const REL_ATTR_RE = /\brel=["']([^"']+)["']/i;
const HREF_ATTR_RE = /\bhref=["']([^"']+)["']/i;
const SRC_ATTR_RE = /\bsrc=["']([^"']+)["']/i;

/**
 * Extracts the same-origin `/_app/...` build assets an `index.html` shell
 * actually references: script sources, modulepreload/stylesheet links, and
 * the inline `import(...)` calls SvelteKit's boot snippet uses to start the
 * app. Anything else (lazy route chunks, external URLs) is left alone.
 */
export function extractShellAssets(html: string): string[] {
	const urls = new Set<string>();
	const addIfShellAsset = (url: string): void => {
		if (url.startsWith('/_app/')) urls.add(url);
	};

	for (const [, attrs] of html.matchAll(LINK_TAG_RE)) {
		const rel = REL_ATTR_RE.exec(attrs)?.[1]?.toLowerCase();
		if (rel !== 'modulepreload' && rel !== 'stylesheet') continue;
		const href = HREF_ATTR_RE.exec(attrs)?.[1];
		if (href) addIfShellAsset(href);
	}

	for (const [, attrs] of html.matchAll(SCRIPT_TAG_RE)) {
		const src = SRC_ATTR_RE.exec(attrs)?.[1];
		if (src) addIfShellAsset(src);
	}

	for (const [, url] of html.matchAll(IMPORT_CALL_RE)) {
		addIfShellAsset(url);
	}

	return [...urls];
}
