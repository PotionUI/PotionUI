import type { AdminGenerationQueue } from '$lib/services/admin-api';
import { formatDuration } from '$lib/utils/format';

export const RUNNING_PAGE_SIZE = 8;
export const SETUP_OWNER_ID = 'setup';
export const SETUP_OWNER_LABEL = 'Setup / recipe';

export type RunningState = 'running' | 'pending';

export interface RunningRow {
	id: string;
	state: RunningState;
	owner: string;
	noTab: boolean;
	preset: string;
	backend: string;
	progress: number | null;
	step: string | null;
	position: number | null;
	sinceMs: number | null;
}

export interface RunningLookups {
	usernameFor: (userId: string) => string | undefined;
	presetNameFor: (presetId: string) => string | undefined;
	backendNameFor: (backendId: string) => string | undefined;
}

export function ownerLabel(userId: string | null, usernameFor: (id: string) => string | undefined): string {
	if (!userId) return 'Unknown';
	if (userId === SETUP_OWNER_ID) return SETUP_OWNER_LABEL;
	return usernameFor(userId) ?? userId;
}

function toMs(epochSeconds: number | null | undefined): number | null {
	return typeof epochSeconds === 'number' && Number.isFinite(epochSeconds) ? epochSeconds * 1000 : null;
}

export function progressPercent(progress: number | null | undefined): number | null {
	if (typeof progress !== 'number' || !Number.isFinite(progress)) return null;
	const percent = progress <= 1 ? progress * 100 : progress;
	return Math.max(0, Math.min(100, Math.round(percent)));
}

export function mapRunningRows(queue: AdminGenerationQueue | null, lookups: RunningLookups): RunningRow[] {
	if (!queue) return [];
	const base = (
		id: string,
		userId: string | null,
		tabId: string | null,
		presetId: string | null,
		backendId: string | null
	) => ({
		id,
		owner: ownerLabel(userId, lookups.usernameFor),
		noTab: !tabId && userId !== SETUP_OWNER_ID,
		preset: presetId ? (lookups.presetNameFor(presetId) ?? presetId) : 'Unknown preset',
		backend: backendId ? (lookups.backendNameFor(backendId) ?? backendId) : '—'
	});

	const running = [...queue.running]
		.sort((a, b) => (a.started_at ?? a.created_at ?? 0) - (b.started_at ?? b.created_at ?? 0))
		.map(
			(r): RunningRow => ({
				...base(r.generation_id, r.user_id, r.tab_id, r.preset_id, r.backend_id),
				state: 'running',
				progress: progressPercent(r.progress),
				step: r.current_step ?? null,
				position: null,
				sinceMs: toMs(r.started_at ?? r.created_at)
			})
		);
	const pending = [...queue.pending]
		.sort((a, b) => a.queue_position - b.queue_position)
		.map(
			(p): RunningRow => ({
				...base(p.generation_id, p.user_id, p.tab_id, p.preset_id, p.backend_id),
				state: 'pending',
				progress: null,
				step: null,
				position: p.queue_position,
				sinceMs: toMs(p.created_at)
			})
		);
	return [...running, ...pending];
}

export function elapsedLabel(sinceMs: number | null, nowMs: number): string {
	if (sinceMs == null) return '—';
	return formatDuration(Math.max(0, nowMs - sinceMs));
}

export function pageSlice<T>(rows: readonly T[], page: number, size = RUNNING_PAGE_SIZE): T[] {
	const start = (Math.max(1, page) - 1) * size;
	return rows.slice(start, start + size);
}

export function totalPages(count: number, size = RUNNING_PAGE_SIZE): number {
	return Math.max(1, Math.ceil(count / size));
}

export function clampPage(page: number, count: number, size = RUNNING_PAGE_SIZE): number {
	return Math.min(Math.max(1, page), totalPages(count, size));
}

export function cancelErrorMessage(error: unknown): string {
	const data = (error as { response?: { data?: { detail?: unknown; message?: unknown } } })?.response?.data;
	const detail = data?.detail;
	if (typeof detail === 'object' && detail !== null && 'message' in detail) {
		const message = (detail as { message?: unknown }).message;
		if (typeof message === 'string' && message) return message;
	}
	if (typeof detail === 'string' && detail) return detail;
	if (typeof data?.message === 'string' && data.message) return data.message;
	const message = (error as { message?: unknown })?.message;
	return typeof message === 'string' && message ? message : 'Could not stop this generation.';
}

export function settleStopping(
	stopping: Record<string, boolean>,
	rows: readonly Pick<RunningRow, 'id'>[]
): Record<string, boolean> {
	const live = new Set(rows.map((r) => r.id));
	const next: Record<string, boolean> = {};
	for (const id of Object.keys(stopping)) if (live.has(id)) next[id] = true;
	return next;
}
