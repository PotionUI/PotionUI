/**
 * Derives WebSocket URLs from the current window.location,
 * so the app works on any host (localhost, LAN IP, etc.)
 * instead of hardcoding localhost:8005.
 */

/**
 * Build a WebSocket URL for the given path (e.g. '/ws/generation').
 * Uses the current page host so it works over LAN.
 */
export function getWsUrl(path: string, token?: string | null): string {
	const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
	const host = window.location.host; // includes port if non-default
	const url = `${protocol}://${host}${path}`;
	if (token) {
		return `${url}?token=${encodeURIComponent(token)}`;
	}
	return url;
}

/**
 * Convert an absolute API URL (e.g. http://localhost:8005/api/...) to a
 * relative path so it routes through the Vite proxy or same-origin server.
 */
export function toRelativeApiUrl(url: string): string | null {
	try {
		const parsed = new URL(url);
		if (parsed.pathname.startsWith('/api/')) {
			return parsed.pathname + parsed.search;
		}
	} catch {
		// not a valid absolute URL
	}
	return null;
}
