import type { GenerationQueueSnapshot, QueueItemLabels } from '$lib/types/api';
import { generationTabs, type Tab } from '$lib/types/tabs';
import { describeOrigin, type OriginInfo } from '$lib/generation/origin';
import { generationTimestampMs } from '$lib/generation/restore/reconcile';

export const ELSEWHERE_LIMIT = 8;

export interface ElsewhereItem {
	generationId: string;
	status: 'pending' | 'running';
	presetName: string | null;
	mode: string | null;
	progress: number | null;
	queuePosition: number | null;
	startedAtMs: number | null;
	origin: OriginInfo | null;
}

export interface ElsewhereList {
	items: ElsewhereItem[];
	total: number;
}

export const EMPTY_ELSEWHERE: ElsewhereList = { items: [], total: 0 };

type QueueEntry = QueueItemLabels & {
	generation_id: string;
	tab_id?: string | null;
	queue_position?: number | null;
	progress?: number | null;
};

export function trackedGenerationIds(tab: Tab): string[] {
	if (tab.kind === 'tool') return [];
	const ids: string[] = [];
	if (tab.activeGenerationId) ids.push(tab.activeGenerationId);
	for (const queued of tab.generation.queue ?? []) ids.push(queued.generation_id);
	ids.push(...Object.keys(tab.directorRunLinks ?? {}));
	return ids;
}

export function findTabFollowing(tabs: readonly Tab[], generationId: string): string | null {
	for (const tab of generationTabs(tabs)) {
		if (tab.openedFrom?.generationId === generationId) return tab.id;
		if (trackedGenerationIds(tab).includes(generationId)) return tab.id;
	}
	return null;
}

function toItem(entry: QueueEntry, status: 'pending' | 'running'): ElsewhereItem {
	return {
		generationId: entry.generation_id,
		status,
		presetName: entry.preset_name ?? null,
		mode: entry.mode ?? null,
		progress: typeof entry.progress === 'number' ? entry.progress : null,
		queuePosition: typeof entry.queue_position === 'number' ? entry.queue_position : null,
		startedAtMs: generationTimestampMs(entry.started_at ?? entry.created_at),
		origin: describeOrigin(entry.origin)
	};
}

function rank(item: ElsewhereItem): number {
	if (item.status === 'running') return item.startedAtMs ?? 0;
	return Number.MAX_SAFE_INTEGER / 2 + (item.queuePosition ?? 0);
}

export function selectRunningElsewhere(
	snapshot: GenerationQueueSnapshot | null | undefined,
	tabs: readonly Tab[],
	limit: number = ELSEWHERE_LIMIT
): ElsewhereList {
	if (!snapshot) return EMPTY_ELSEWHERE;
	const openTabIds = new Set(generationTabs(tabs).map((tab) => tab.id));
	const followed = new Set(generationTabs(tabs).flatMap((tab) => [...trackedGenerationIds(tab), ...(tab.openedFrom ? [tab.openedFrom.generationId] : [])]));
	const entries: Array<[QueueEntry, 'pending' | 'running']> = [
		...snapshot.running.map((entry): [QueueEntry, 'running'] => [entry, 'running']),
		...snapshot.pending.map((entry): [QueueEntry, 'pending'] => [entry, 'pending'])
	];
	const items = entries
		.filter(([entry]) => entry.origin?.kind === 'mcp')
		.filter(([entry]) => !followed.has(entry.generation_id))
		.filter(([entry]) => entry.has_tab === false || !entry.tab_id || !openTabIds.has(entry.tab_id))
		.map(([entry, status]) => toItem(entry, status))
		.sort((a, b) => rank(a) - rank(b));
	return { items: items.slice(0, limit), total: items.length };
}

export function formatElapsed(startedAtMs: number | null, nowMs: number): string {
	if (startedAtMs === null) return '';
	const seconds = Math.max(0, Math.floor((nowMs - startedAtMs) / 1000));
	if (seconds < 60) return `${seconds}s`;
	const minutes = Math.floor(seconds / 60);
	if (minutes < 60) return `${minutes}m ${String(seconds % 60).padStart(2, '0')}s`;
	return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;
}
