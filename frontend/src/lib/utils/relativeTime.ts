// Compact relative timestamps for mono micro-labels ("35M AGO", "2H AGO", "JUL 3").
// Output is intentionally uppercase-friendly — callers render it in tracked mono.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const HAS_TZ_DESIGNATOR = /(?:Z|[+-]\d{2}:?\d{2})$/i;
const SQLITE_DATETIME = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2}(?:\.\d+)?)$/;

/**
 * Parse a server-provided timestamp as UTC when it carries no timezone
 * designator (the backend has emitted offset-less strings; plugins, cached
 * data, and websocket payloads may still). A string that already carries an
 * offset or "Z" parses as-is. Numbers and Dates pass through unchanged.
 * Returns null for empty/invalid input instead of an Invalid Date.
 */
export function parseServerDate(value: string | number | Date | null | undefined): Date | null {
	if (value == null || value === '') return null;
	if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
	if (typeof value === 'number') {
		const date = new Date(value);
		return Number.isNaN(date.getTime()) ? null : date;
	}

	let normalized = value;
	const sqliteMatch = SQLITE_DATETIME.exec(value);
	if (sqliteMatch) normalized = `${sqliteMatch[1]}T${sqliteMatch[2]}`;

	const date = HAS_TZ_DESIGNATOR.test(normalized) ? new Date(normalized) : new Date(`${normalized}Z`);
	return Number.isNaN(date.getTime()) ? null : date;
}

export function timeAgo(dateString?: string, now: Date = new Date()): string {
	if (!dateString) return '';
	const date = parseServerDate(dateString);
	if (!date) return '';

	const diffMs = now.getTime() - date.getTime();
	const minutes = Math.floor(diffMs / 60_000);
	if (minutes < 1) return 'now';
	if (minutes < 60) return `${minutes}m ago`;
	const hours = Math.floor(minutes / 60);
	if (hours < 24) return `${hours}h ago`;
	const days = Math.floor(hours / 24);
	if (days < 7) return `${days}d ago`;

	const label = `${MONTHS[date.getMonth()]} ${date.getDate()}`;
	return date.getFullYear() === now.getFullYear() ? label : `${label} ${date.getFullYear()}`;
}

/** Day bucket label for gallery group headers: "Today", "Yesterday", "Jul 3", "Jul 3 2025". */
export function dayLabel(dateString: string, now: Date = new Date()): string {
	const date = parseServerDate(dateString);
	if (!date) return 'Unknown';

	const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
	const dayDiff = Math.round((startOfDay(now) - startOfDay(date)) / 86_400_000);
	if (dayDiff === 0) return 'Today';
	if (dayDiff === 1) return 'Yesterday';

	const label = `${MONTHS[date.getMonth()]} ${date.getDate()}`;
	return date.getFullYear() === now.getFullYear() ? label : `${label} ${date.getFullYear()}`;
}

/** Stable key for grouping items by local calendar day. */
export function dayKey(dateString: string): string {
	const date = parseServerDate(dateString);
	if (!date) return 'unknown';
	return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}
