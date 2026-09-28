import type { IndexingStatus } from '$lib/services/api/models';

const RUNNING_STATES = new Set(['scanning', 'indexing']);

export type IndexingDoneSummary =
	| { kind: 'zero' }
	| { kind: 'up_to_date'; found: number }
	| { kind: 'new_indexed'; indexed: number; alreadyIndexed: number; found: number };

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

export function indexingDoneSummary(status: IndexingStatus | null): IndexingDoneSummary | null {
	if (!status || status.state !== 'done') return null;
	const found = status.found_on_disk ?? 0;
	if (found === 0) return { kind: 'zero' };
	const indexed = status.indexed ?? 0;
	if (indexed === 0) return { kind: 'up_to_date', found };
	return { kind: 'new_indexed', indexed, alreadyIndexed: found - indexed, found };
}
