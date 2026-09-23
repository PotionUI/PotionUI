import { describe, it, expect } from 'vitest';
import type { Download } from '$lib/stores/downloads';
import {
	DEFAULT_DOWNLOADER_FILTERS,
	applyDownloaderFilters,
	clearAllDownloaderFilters,
	clearDownloaderFilterChip,
	downloaderFilterActiveCount,
	downloaderFilterChips,
	type DownloaderFilters
} from './downloaderFilters';

function download(overrides: Partial<Download> = {}): Download {
	return {
		id: 'd1',
		type: 'model',
		url: 'https://example.com/model.safetensors',
		destination_path: '/models/checkpoint/model.safetensors',
		filename: 'model.safetensors',
		status: 'pending',
		progress: 0,
		total_bytes: null,
		downloaded_bytes: 0,
		speed_bytes_per_sec: null,
		error_message: null,
		provider_id: null,
		tags: [],
		checksum_sha256: null,
		retry_count: 0,
		group_id: null,
		repo_id: null,
		created_at: '2026-09-01T00:00:00.000Z',
		started_at: null,
		completed_at: null,
		created_by: null,
		...overrides
	};
}

describe('applyDownloaderFilters', () => {
	const downloading = download({ id: 'active-1', filename: 'unet.safetensors', status: 'downloading' });
	const paused = download({ id: 'active-2', filename: 'lora.safetensors', status: 'paused' });
	const pending = download({ id: 'pending-1', filename: 'clip.safetensors', status: 'pending' });
	const completed = download({ id: 'done-1', filename: 'vae.safetensors', status: 'completed' });
	const failed = download({ id: 'fail-1', filename: 'controlnet.safetensors', status: 'failed' });
	const cancelled = download({ id: 'fail-2', filename: 'refiner.safetensors', status: 'cancelled' });
	const all = [downloading, paused, pending, completed, failed, cancelled];

	it('passes everything through when status is all and query is empty', () => {
		expect(applyDownloaderFilters(all, DEFAULT_DOWNLOADER_FILTERS).map((d) => d.id)).toEqual(
			all.map((d) => d.id)
		);
	});

	it('buckets active as downloading or paused', () => {
		const rows = applyDownloaderFilters(all, { ...DEFAULT_DOWNLOADER_FILTERS, status: 'active' });
		expect(rows.map((d) => d.id).sort()).toEqual(['active-1', 'active-2']);
	});

	it('buckets failed as failed or cancelled', () => {
		const rows = applyDownloaderFilters(all, { ...DEFAULT_DOWNLOADER_FILTERS, status: 'failed' });
		expect(rows.map((d) => d.id).sort()).toEqual(['fail-1', 'fail-2']);
	});

	it('matches the search against filename and url', () => {
		const byFilename = applyDownloaderFilters(all, { ...DEFAULT_DOWNLOADER_FILTERS, q: 'vae' });
		expect(byFilename.map((d) => d.id)).toEqual(['done-1']);

		const byUrl = applyDownloaderFilters(
			[download({ id: 'u1', filename: 'x.safetensors', url: 'https://civitai.com/models/123' })],
			{ ...DEFAULT_DOWNLOADER_FILTERS, q: 'civitai' }
		);
		expect(byUrl.map((d) => d.id)).toEqual(['u1']);
	});

	it('keeps the default insertion order when sortBy is created_at', () => {
		expect(applyDownloaderFilters(all, DEFAULT_DOWNLOADER_FILTERS).map((d) => d.id)).toEqual(
			all.map((d) => d.id)
		);
	});

	it('sorts by filename A-Z when sortBy is filename', () => {
		const rows = applyDownloaderFilters(all, { ...DEFAULT_DOWNLOADER_FILTERS, sortBy: 'filename' });
		expect(rows.map((d) => d.filename)).toEqual([
			'clip.safetensors',
			'controlnet.safetensors',
			'lora.safetensors',
			'refiner.safetensors',
			'unet.safetensors',
			'vae.safetensors'
		]);
	});

	it('does not mutate the list it was given', () => {
		const source = [...all];
		applyDownloaderFilters(source, { ...DEFAULT_DOWNLOADER_FILTERS, sortBy: 'filename' });
		expect(source.map((d) => d.id)).toEqual(all.map((d) => d.id));
	});
});

describe('downloader filter chips', () => {
	it('emits a status chip labelled from the segmented option, and clears it', () => {
		const filters: DownloaderFilters = { q: 'x', status: 'failed', sortBy: 'created_at' };
		expect(downloaderFilterChips(filters)).toEqual([{ key: 'status', label: 'Failed' }]);
		expect(clearDownloaderFilterChip(filters, 'status').status).toBe('all');
		expect(clearDownloaderFilterChip(filters, 'nothing')).toBe(filters);
	});

	it('emits no chips and zero active count for the default filters', () => {
		expect(downloaderFilterChips(DEFAULT_DOWNLOADER_FILTERS)).toEqual([]);
		expect(downloaderFilterActiveCount(DEFAULT_DOWNLOADER_FILTERS)).toBe(0);
	});

	it('counts status as one active filter and keeps query and sort when clearing all', () => {
		const filters: DownloaderFilters = { q: 'keep me', status: 'completed', sortBy: 'filename' };
		expect(downloaderFilterActiveCount(filters)).toBe(1);
		expect(clearAllDownloaderFilters(filters)).toEqual({ q: 'keep me', status: 'all', sortBy: 'filename' });
	});
});
