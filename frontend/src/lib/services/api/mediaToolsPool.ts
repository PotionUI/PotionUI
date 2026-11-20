import { api } from '$lib/services/api/index';
import type { APIResponse } from '$lib/types/api';

export type PoolLimitId =
	| 'max_concurrent'
	| 'max_previews'
	| 'max_running_per_user'
	| 'max_queued_per_user'
	| 'ffmpeg_threads';

export type MediaToolPoolLimits = Record<PoolLimitId, number>;

export interface MediaToolPoolJob {
	run_id: string;
	user_id: string;
	username: string | null;
	kind: 'run' | 'preview';
	status: 'queued' | 'running';
	tools: string[];
	label: string | null;
	progress: number | null;
	queued_at: number | null;
	started_at: number | null;
	elapsed_seconds: number;
	waited_seconds: number;
}

export interface MediaToolPoolMetrics {
	window_minutes: number;
	peak_concurrent: number;
	peak_queued: number;
	wait_avg_seconds: number;
	wait_max_seconds: number;
	started_jobs: number;
	rejected_queue_full: number;
	rejected_preview_busy: number;
	utilisation_5m_percent: number | null;
	utilisation_60m_percent: number | null;
}

export interface MediaToolPool {
	limits: MediaToolPoolLimits;
	running: number;
	queued: number;
	previews: { limit: number; in_use: number; clips_running: number };
	cpu_cores: number;
	setting_keys: Record<PoolLimitId, string>;
	jobs: MediaToolPoolJob[];
	metrics: MediaToolPoolMetrics;
}

function unwrap<T>(body: APIResponse<T> | undefined, fallback: string): T {
	if (!body || !body.success || body.data === undefined) {
		throw new Error(body?.message || body?.error || fallback);
	}
	return body.data;
}

export async function getMediaToolPool(): Promise<MediaToolPool> {
	const response = await api.getClient().get('/api/admin/media-tools/pool');
	return unwrap<MediaToolPool>(response.data, 'Could not load the media tools pool.');
}

export async function cancelMediaToolPoolJob(runId: string): Promise<void> {
	const response = await api
		.getClient()
		.post(`/api/admin/media-tools/jobs/${encodeURIComponent(runId)}/cancel`);
	unwrap(response.data, 'Could not cancel the job.');
}

export async function saveMediaToolPoolSetting(key: string, value: number): Promise<void> {
	const response = await api.getClient().put(`/api/settings/${encodeURIComponent(key)}`, { value });
	const body = response.data as APIResponse | undefined;
	if (!body || !body.success) throw new Error(body?.message || body?.error || 'Could not save the setting.');
}
