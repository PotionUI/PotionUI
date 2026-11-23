const MEDIA_PATH = /^\/api\/media\/(generations|files)\//;
const STILL_EXTENSION = /\.(png|jpe?g|avif|bmp)$/i;

export function occurrenceKeys(urls: ReadonlyArray<string | null | undefined>): string[] {
	const seen = new Map<string, number>();
	return urls.map((raw) => {
		const url = raw ?? '';
		const n = seen.get(url) ?? 0;
		seen.set(url, n + 1);
		return `${url}#${n}`;
	});
}

const baseOrigin =() => (typeof location === 'undefined' ? 'http://localhost' : location.origin);

export function tileThumbUrl(url: string | null | undefined): string {
	if (!url) return '';
	let parsed: URL;
	try {
		parsed = new URL(url, baseOrigin());
	} catch {
		return url;
	}
	if (parsed.origin !== baseOrigin() || !MEDIA_PATH.test(parsed.pathname) || !STILL_EXTENSION.test(parsed.pathname)) return url;
	if (parsed.searchParams.has('size')) return url;
	const hash = parsed.hash;
	const separator = url.includes('?') ? '&' : '?';
	const bare = hash ? url.slice(0, url.indexOf('#')) : url;
	return `${bare}${separator}size=medium${hash}`;
}
