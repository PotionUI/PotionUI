export function msUntil(resetsAt: string | null | undefined, now: number): number | null {
	if (!resetsAt) return null;
	const target = Date.parse(resetsAt);
	if (Number.isNaN(target)) return null;
	return Math.max(0, target - now);
}

export function formatCountdown(ms: number): string {
	const total = Math.ceil(Math.max(0, ms) / 1000);
	const hours = Math.floor(total / 3600);
	const minutes = Math.floor((total % 3600) / 60);
	const seconds = total % 60;
	if (hours > 0) return `${hours} h ${minutes} min`;
	if (minutes > 0) return `${minutes} min ${seconds} s`;
	return `${seconds} s`;
}

export function formatResetsIn(ms: number, resetsAt: string): string {
	if (ms > 36 * 3600 * 1000) {
		const date = new Date(resetsAt).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
		return `resets ${date}`;
	}
	const minutes = Math.ceil(ms / 60000);
	if (minutes >= 60) return `resets in ${Math.round(minutes / 60)} h`;
	return `resets in ${Math.max(1, minutes)} min`;
}
