import { formatDuration } from '$lib/utils/format';
import type {
	MediaToolPool,
	MediaToolPoolJob,
	PoolLimitId
} from '$lib/services/api/mediaToolsPool';

export const POOL_POLL_MS = 3000;
export const POOL_MAX_ROWS = 50;
export const POOL_SETTING_MAX = 256;

const BUSY_PERCENT = 70;
const SLOW_START_SECONDS = 20;

export type PoolTone = 'ok' | 'warning' | 'danger';

export interface PoolAssessment {
	tone: PoolTone;
	message: string;
}

export function formatWait(seconds: number): string {
	if (!seconds || seconds <= 0) return '0s';
	return formatDuration(seconds * 1000);
}

export function formatPercent(value: number | null | undefined): string {
	return value == null ? '—' : `${Math.round(value)}%`;
}

export function assessPool(pool: MediaToolPool): PoolAssessment {
	const { metrics, limits } = pool;
	const full = metrics.rejected_queue_full;
	const busyPreviews = metrics.rejected_preview_busy;
	const rejected = full + busyPreviews;
	const util60 = metrics.utilisation_60m_percent ?? 0;

	if (full > 0) {
		return {
			tone: 'danger',
			message: `${full} ${full === 1 ? 'request was' : 'requests were'} refused in the last hour because users had too many runs waiting. Raise concurrent runs or runs queued per user below.`
		};
	}
	if (busyPreviews > 0) {
		return {
			tone: 'danger',
			message: `${busyPreviews} ${busyPreviews === 1 ? 'preview was' : 'previews were'} refused in the last hour because all preview slots were busy. Raise concurrent previews below.`
		};
	}
	if (rejected === 0 && pool.queued > 0 && util60 >= BUSY_PERCENT) {
		return {
			tone: 'warning',
			message: `${pool.queued} waiting and the pool was busy ${Math.round(util60)}% of the last hour. Raise concurrent runs or ffmpeg threads below.`
		};
	}
	if (metrics.started_jobs > 0 && metrics.wait_avg_seconds >= SLOW_START_SECONDS) {
		return {
			tone: 'warning',
			message: `Tools waited ${formatWait(metrics.wait_avg_seconds)} on average in the last hour. Raise concurrent runs or ffmpeg threads below.`
		};
	}
	if (pool.running === 0 && pool.queued === 0) {
		return { tone: 'ok', message: 'The pool is idle.' };
	}
	return {
		tone: 'ok',
		message: `The pool has room. Peak was ${metrics.peak_concurrent} of ${limits.max_concurrent} runs in the last hour.`
	};
}

export interface PoolRow {
	id: string;
	user: string;
	name: string;
	kind: 'run' | 'preview';
	state: 'running' | 'queued';
	position: number | null;
	progress: number | null;
	seconds: number;
}

function toolName(id: string): string {
	const spaced = id.replace(/[_-]+/g, ' ').trim();
	return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : id;
}

export function jobName(job: Pick<MediaToolPoolJob, 'label' | 'tools'>): string {
	if (job.label) return job.label;
	if (job.tools.length > 0) return job.tools.map(toolName).join(', ');
	return 'Media tool';
}

export function mapPoolRows(
	jobs: readonly MediaToolPoolJob[],
	fetchedAtMs: number,
	nowMs: number
): PoolRow[] {
	const drift = Math.max(0, nowMs - fetchedAtMs) / 1000;
	const ordered = [
		...jobs.filter((job) => job.status === 'running'),
		...jobs.filter((job) => job.status === 'queued')
	];
	let position = 0;
	return ordered.map((job) => {
		const queued = job.status === 'queued';
		return {
			id: job.run_id,
			user: job.username || 'Unknown user',
			name: jobName(job),
			kind: job.kind,
			state: job.status,
			position: queued ? ++position : null,
			progress:
				!queued && job.progress != null ? Math.round(Math.min(1, Math.max(0, job.progress)) * 100) : null,
			seconds: (queued ? job.waited_seconds : job.elapsed_seconds) + drift
		};
	});
}

export interface PoolLimitField {
	id: PoolLimitId;
	label: string;
	description: string;
	min: number;
}

export const POOL_LIMIT_FIELDS: readonly PoolLimitField[] = [
	{ id: 'max_concurrent', label: 'Concurrent runs', description: 'Runs that may encode at the same time.', min: 0 },
	{ id: 'max_previews', label: 'Concurrent previews', description: 'Live previews that may render at the same time.', min: 1 },
	{ id: 'max_running_per_user', label: 'Runs per user', description: 'Runs one user may encode at once.', min: 0 },
	{ id: 'max_queued_per_user', label: 'Queued per user', description: 'Runs one user may have waiting before new ones are refused.', min: 1 },
	{ id: 'ffmpeg_threads', label: 'ffmpeg threads', description: 'CPU threads each ffmpeg process may use.', min: 0 }
];

export const POOL_LIMIT_DEFAULTS: Record<PoolLimitId, number> = {
	max_concurrent: 0,
	max_previews: 2,
	max_running_per_user: 0,
	max_queued_per_user: 8,
	ffmpeg_threads: 0
};

export const POOL_LIMIT_KEYS: Record<PoolLimitId, string> = {
	max_concurrent: 'media_tools_max_concurrent',
	max_previews: 'media_tools_max_previews',
	max_running_per_user: 'media_tools_max_running_per_user',
	max_queued_per_user: 'media_tools_max_queued_per_user',
	ffmpeg_threads: 'media_tools_ffmpeg_threads'
};

export function parsePoolLimit(raw: string, min: number): number | null {
	const text = raw.trim();
	if (!/^\d+$/.test(text)) return null;
	const value = Number(text);
	return value >= min && value <= POOL_SETTING_MAX ? value : null;
}

export function limitHint(id: PoolLimitId, value: number, pool: MediaToolPool): string {
	if (value > 0) return '';
	if (id === 'max_concurrent') return `Auto (${pool.limits.max_concurrent} on this machine)`;
	if (id === 'ffmpeg_threads') return `Auto (${pool.limits.ffmpeg_threads} on this machine)`;
	if (id === 'max_running_per_user') return 'No cap';
	return '';
}
