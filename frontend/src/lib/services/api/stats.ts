import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';

/** Every stats endpoint is scoped by the same inclusive date range (YYYY-MM-DD). */
export interface StatsRange {
	from?: string;
	to?: string;
}

export type StatsMetric = 'count' | 'duration' | 'bytes';
export type StatsBucket = 'day' | 'week' | 'month';

export type StatsDimension =
	| 'preset'
	| 'model'
	| 'model_type'
	| 'sampler'
	| 'scheduler'
	| 'steps'
	| 'cfg'
	| 'resolution'
	| 'denoise'
	| 'mode'
	| 'status';

export interface StatsOverview {
	total_generations: number;
	completed: number;
	failed: number;
	active_days: number;
	first_generation_at: string | null;
	last_generation_at: string | null;
	total_outputs: number;
	total_bytes: number;
	image_bytes: number;
	video_bytes: number;
	distinct_models: number;
	/** Milliseconds. Null when no generation in range has a recorded duration. */
	avg_duration_ms: number | null;
	median_duration_ms: number | null;
	p95_duration_ms: number | null;
}

export interface TimeseriesPoint {
	bucket: string;
	value: number;
}

export interface StatsTimeseries {
	metric: StatsMetric;
	bucket: StatsBucket;
	points: TimeseriesPoint[];
}

export interface BreakdownItem {
	/** Stable identity — hash this for color, so a rename never repaints the series. */
	key: string;
	/** Human-facing name. Preset ULIDs resolve to their preset.yml name. */
	label: string;
	/** The model_type when dimension is `model`; otherwise null. */
	group: string | null;
	count: number;
}

export interface StatsBreakdown {
	dimension: StatsDimension;
	items: BreakdownItem[];
	total_distinct: number;
}

export interface DurationBucket {
	lower_s: number;
	/** Null on the final, open-ended bin. */
	upper_s: number | null;
	count: number;
}

export interface StatsDurations {
	buckets: DurationBucket[];
	/** Generations with no recorded duration. Never fold these into a bin. */
	unknown: number;
	p50_ms: number | null;
	p95_ms: number | null;
	p99_ms: number | null;
}

export interface StorageByType {
	file_type: 'IMAGE' | 'VIDEO';
	count: number;
	bytes: number;
}

export interface StatsStorage {
	by_type: StorageByType[];
	over_time: { bucket: string; image_bytes: number; video_bytes: number }[];
	top_resolutions: { label: string; width: number; height: number; count: number; bytes: number }[];
	avg_file_bytes: number;
}

/**
 * Durable, generation-independent stats: written once at
 * generation completion into `generation_stats`, so these keep reporting
 * even after the underlying generation is deleted. Every numeric field is
 * `null`, never a guessed 0/false, when that run's capture wasn't available
 * (e.g. cold/warm needs a native-engine model-cache lease; resources need a
 * CUDA device).
 */
export interface PresetTimingItem {
	preset_id: string;
	preset_name: string;
	total_runs: number;
	cold_runs: number;
	warm_runs: number;
	/** Milliseconds. */
	avg_cold_duration_ms: number | null;
	avg_warm_duration_ms: number | null;
	avg_model_load_ms: number | null;
}

export interface StatsPresetTiming {
	items: PresetTimingItem[];
}

export interface PresetResourcesItem {
	preset_id: string;
	preset_name: string;
	total_runs: number;
	peak_vram_mb: number | null;
	avg_vram_mb: number | null;
	peak_ram_mb: number | null;
	avg_ram_mb: number | null;
	avg_cpu_percent: number | null;
}

export interface StatsPresetResources {
	items: PresetResourcesItem[];
}

function rangeParams(range?: StatsRange): URLSearchParams {
	const params = new URLSearchParams();
	if (range?.from) params.append('from', range.from);
	if (range?.to) params.append('to', range.to);
	return params;
}

function withQuery(path: string, params: URLSearchParams): string {
	const qs = params.toString();
	return qs ? `${path}?${qs}` : path;
}

export function createStatsApi(client: AxiosInstance) {
	return {
		async getStatsOverview(range?: StatsRange): Promise<APIResponse<StatsOverview>> {
			const response = await client.get(withQuery('/api/stats/overview', rangeParams(range)));
			return response.data;
		},

		async getStatsTimeseries(
			metric: StatsMetric = 'count',
			bucket: StatsBucket = 'day',
			range?: StatsRange
		): Promise<APIResponse<StatsTimeseries>> {
			const params = rangeParams(range);
			params.append('metric', metric);
			params.append('bucket', bucket);
			const response = await client.get(withQuery('/api/stats/timeseries', params));
			return response.data;
		},

		async getStatsBreakdown(
			dimension: StatsDimension,
			limit = 10,
			range?: StatsRange
		): Promise<APIResponse<StatsBreakdown>> {
			const params = rangeParams(range);
			params.append('dimension', dimension);
			params.append('limit', limit.toString());
			const response = await client.get(withQuery('/api/stats/breakdown', params));
			return response.data;
		},

		async getStatsDurations(range?: StatsRange): Promise<APIResponse<StatsDurations>> {
			const response = await client.get(withQuery('/api/stats/durations', rangeParams(range)));
			return response.data;
		},

		async getStatsStorage(
			range?: StatsRange,
			bucket: StatsBucket = 'day',
			limit = 30
		): Promise<APIResponse<StatsStorage>> {
			const params = rangeParams(range);
			params.append('bucket', bucket);
			params.append('limit', limit.toString());
			const response = await client.get(withQuery('/api/stats/storage', params));
			return response.data;
		},

		/** Cold vs. warm start counts/averages per preset (durable generation_stats store). */
		async getStatsPresetTiming(limit = 10): Promise<APIResponse<StatsPresetTiming>> {
			const params = new URLSearchParams();
			params.append('limit', limit.toString());
			const response = await client.get(withQuery('/api/stats/presets/timing', params));
			return response.data;
		},

		/** Peak/average VRAM, RAM and CPU per preset (durable generation_stats store). */
		async getStatsPresetResources(limit = 10): Promise<APIResponse<StatsPresetResources>> {
			const params = new URLSearchParams();
			params.append('limit', limit.toString());
			const response = await client.get(withQuery('/api/stats/presets/resources', params));
			return response.data;
		}
	};
}
