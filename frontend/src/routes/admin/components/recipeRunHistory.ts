import type { SetupRun } from '$lib/services/api/setup';
import { formatDuration } from '$lib/utils/format';

/** How long a run took, or has been going. `null` when it never started, or
 * when the timestamps are unusable (unparseable, or finishing before it
 * began), so a nonsense negative duration is never rendered. */
export function runDuration(
	run: Pick<SetupRun, 'created_at' | 'completed_at'>,
	now: () => number = Date.now
): string | null {
	if (!run.created_at) return null;
	const start = Date.parse(run.created_at);
	if (Number.isNaN(start)) return null;
	const end = run.completed_at ? Date.parse(run.completed_at) : now();
	if (Number.isNaN(end) || end < start) return null;
	return formatDuration(end - start);
}

/** A run's start as a short local date + time, or an em dash when it has none.
 * Mono/tabular-nums-ready — no weekday names, no relative phrasing. */
export function runStartedLabel(run: Pick<SetupRun, 'created_at'>): string {
	if (!run.created_at) return '—';
	const parsed = new Date(run.created_at);
	if (Number.isNaN(parsed.getTime())) return '—';
	return parsed.toLocaleString(undefined, {
		year: 'numeric',
		month: '2-digit',
		day: '2-digit',
		hour: '2-digit',
		minute: '2-digit'
	});
}

/**
 * Merge a live run into a history list so the Runs section never shows the
 * same run twice — the active run arrives both from the history endpoint and
 * from whatever the last action returned, and the live copy is the fresher of
 * the two. Order is preserved (the endpoint returns newest first); a live run
 * the history doesn't know about yet goes to the front.
 */
export function mergeRunHistory<T extends { id: string }>(history: T[], live: T | null): T[] {
	if (!live) return history;
	const index = history.findIndex((entry) => entry.id === live.id);
	if (index === -1) return [live, ...history];
	const merged = [...history];
	merged[index] = live;
	return merged;
}
