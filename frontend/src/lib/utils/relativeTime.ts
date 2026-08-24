// Compact relative timestamps for mono micro-labels ("35M AGO", "2H AGO", "JUL 3").
// Output is intentionally uppercase-friendly — callers render it in tracked mono.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function timeAgo(dateString?: string, now: Date = new Date()): string {
	if (!dateString) return '';
	const date = new Date(dateString);
	if (Number.isNaN(date.getTime())) return '';

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
	const date = new Date(dateString);
	if (Number.isNaN(date.getTime())) return 'Unknown';

	const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
	const dayDiff = Math.round((startOfDay(now) - startOfDay(date)) / 86_400_000);
	if (dayDiff === 0) return 'Today';
	if (dayDiff === 1) return 'Yesterday';

	const label = `${MONTHS[date.getMonth()]} ${date.getDate()}`;
	return date.getFullYear() === now.getFullYear() ? label : `${label} ${date.getFullYear()}`;
}

/** Stable key for grouping items by local calendar day. */
export function dayKey(dateString: string): string {
	const date = new Date(dateString);
	if (Number.isNaN(date.getTime())) return 'unknown';
	return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}
