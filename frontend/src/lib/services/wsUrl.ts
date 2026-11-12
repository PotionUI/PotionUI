/**
 * Derives WebSocket URLs from the current window.location,
 * so the app works on any host (localhost, LAN IP, etc.)
 * instead of hardcoding localhost:7680.
 */

/**
 * Build a WebSocket URL for the given path (e.g. '/ws/generation').
 * Uses the current page host so it works over LAN.
 */
export function getWsUrl(path: string): string {
	const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
	const host = window.location.host; // includes port if non-default
	return `${protocol}://${host}${path}`;
}
