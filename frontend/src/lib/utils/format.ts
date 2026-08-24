/**
 * Shared formatting helpers for bytes, durations, and counts.
 * Lifted from the downloads store's (formerly duplicated) formatBytes logic.
 */

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];

export function formatBytes(bytes: number, decimals = 2): string {
	if (!bytes || bytes <= 0) return '0 B';
	const k = 1024;
	const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), BYTE_UNITS.length - 1);
	const value = bytes / Math.pow(k, i);
	const rounded = i === 0 ? value.toFixed(0) : value.toFixed(decimals);
	return `${parseFloat(rounded)} ${BYTE_UNITS[i]}`;
}

export function formatDuration(ms: number): string {
	if (!ms || ms <= 0) return '0s';
	const seconds = ms / 1000;

	if (seconds < 1) return `${seconds.toFixed(1)}s`;
	if (seconds < 60) return `${Math.round(seconds)}s`;

	const totalSeconds = Math.floor(seconds);
	if (totalSeconds < 3600) {
		const m = Math.floor(totalSeconds / 60);
		const s = totalSeconds % 60;
		return s > 0 ? `${m}m ${s}s` : `${m}m`;
	}

	const h = Math.floor(totalSeconds / 3600);
	const m = Math.floor((totalSeconds % 3600) / 60);
	return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

export function formatCount(n: number): string {
	return new Intl.NumberFormat('en-US').format(n);
}

/**
 * Format a duration given in seconds (as opposed to `formatDuration`'s
 * milliseconds), with one decimal place below a minute. Generated video clips
 * are almost always a handful of seconds, where whole-second rounding hides
 * the difference between e.g. a 2s and a 2.8s clip.
 */
export function formatSeconds(seconds: number): string {
	if (!seconds || seconds <= 0) return '0s';
	if (seconds < 60) return `${seconds.toFixed(1)}s`;
	return formatDuration(seconds * 1000);
}
