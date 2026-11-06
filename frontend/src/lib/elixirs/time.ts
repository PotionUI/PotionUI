export function relativeTime(value?: string | number | Date | null): string {
	if (!value) return '';
	const then = new Date(value).getTime();
	if (Number.isNaN(then)) return '';
	const seconds = Math.round((Date.now() - then) / 1000);
	if (seconds < 60) return 'just now';
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `${minutes} min ago`;
	const hours = Math.round(minutes / 60);
	if (hours < 24) return `${hours} h ago`;
	const days = Math.round(hours / 24);
	if (days < 30) return `${days} d ago`;
	return new Date(value).toLocaleDateString();
}
