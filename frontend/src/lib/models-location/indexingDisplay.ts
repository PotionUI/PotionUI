import type { IndexingStatus } from '$lib/services/api/models';

const RUNNING_STATES = new Set(['scanning', 'indexing']);

export function indexingIsRunning(status: IndexingStatus | null): boolean {
	if (!status) return false;
	return RUNNING_STATES.has(status.state) || status.restart_pending === true;
}

export function indexingIsVisible(status: IndexingStatus | null): boolean {
	return !!status && status.state !== 'idle';
}

export function indexingPercent(status: IndexingStatus | null): number | null {
	if (!status || status.state !== 'indexing') return null;
	const total = status.total ?? 0;
	if (total <= 0) return null;
	const processed = status.processed ?? 0;
	return Math.max(0, Math.min(100, Math.round((processed / total) * 100)));
}
