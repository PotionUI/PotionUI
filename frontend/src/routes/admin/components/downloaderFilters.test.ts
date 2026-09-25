import { describe, it, expect } from 'vitest';
import type { Download } from '$lib/stores/downloads';
import {
	DEFAULT_DOWNLOADER_FILTERS,
	applyDownloaderFilters,
	downloadSectionCounts,
	matchesDownloadSection,
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

	it('passes everything through for the "all" section with an empty query', () => {
		expect(applyDownloaderFilters(all, DEFAULT_DOWNLOADER_FILTERS, 'all').map((d) => d.id)).toEqual(
			all.map((d) => d.id)
		);
	});

	it('buckets the active section as downloading or paused', () => {
		const rows = applyDownloaderFilters(all, DEFAULT_DOWNLOADER_FILTERS, 'active');
		expect(rows.map((d) => d.id).sort()).toEqual(['active-1', 'active-2']);
	});

	it('buckets the failed section as failed or cancelled', () => {
		const rows = applyDownloaderFilters(all, DEFAULT_DOWNLOADER_FILTERS, 'failed');
		expect(rows.map((d) => d.id).sort()).toEqual(['fail-1', 'fail-2']);
	});

	it('matches the search against filename and url within the selected section', () => {
		const byFilename = applyDownloaderFilters(all, { ...DEFAULT_DOWNLOADER_FILTERS, q: 'vae' }, 'all');
		expect(byFilename.map((d) => d.id)).toEqual(['done-1']);

		const byUrl = applyDownloaderFilters(
			[download({ id: 'u1', filename: 'x.safetensors', url: 'https://civitai.com/models/123' })],
			{ ...DEFAULT_DOWNLOADER_FILTERS, q: 'civitai' },
			'all'
		);
		expect(byUrl.map((d) => d.id)).toEqual(['u1']);

		expect(applyDownloaderFilters(all, { ...DEFAULT_DOWNLOADER_FILTERS, q: 'vae' }, 'failed')).toEqual([]);
	});

	it('keeps the default insertion order when sortBy is created_at', () => {
		expect(applyDownloaderFilters(all, DEFAULT_DOWNLOADER_FILTERS, 'all').map((d) => d.id)).toEqual(
			all.map((d) => d.id)
		);
	});

	it('sorts by filename A-Z when sortBy is filename', () => {
		const filters: DownloaderFilters = { ...DEFAULT_DOWNLOADER_FILTERS, sortBy: 'filename' };
		const rows = applyDownloaderFilters(all, filters, 'all');
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
		applyDownloaderFilters(source, { ...DEFAULT_DOWNLOADER_FILTERS, sortBy: 'filename' }, 'all');
		expect(source.map((d) => d.id)).toEqual(all.map((d) => d.id));
	});
});

describe('matchesDownloadSection', () => {
	it('treats every status as a match for "all"', () => {
		expect(matchesDownloadSection({ status: 'failed' }, 'all')).toBe(true);
	});

	it('buckets pending and completed as themselves only', () => {
		expect(matchesDownloadSection({ status: 'pending' }, 'pending')).toBe(true);
		expect(matchesDownloadSection({ status: 'downloading' }, 'pending')).toBe(false);
		expect(matchesDownloadSection({ status: 'completed' }, 'completed')).toBe(true);
		expect(matchesDownloadSection({ status: 'failed' }, 'completed')).toBe(false);
	});
});

describe('downloadSectionCounts', () => {
	it('counts every section including the total under "all"', () => {
		const all = [
			download({ id: 'a', status: 'downloading' }),
			download({ id: 'b', status: 'paused' }),
			download({ id: 'c', status: 'pending' }),
			download({ id: 'd', status: 'completed' }),
			download({ id: 'e', status: 'failed' }),
			download({ id: 'f', status: 'cancelled' })
		];
		expect(downloadSectionCounts(all)).toEqual({
			all: 6,
			active: 2,
			pending: 1,
			completed: 1,
			failed: 2
		});
	});

	it('returns all zero counts for an empty list', () => {
		expect(downloadSectionCounts([])).toEqual({ all: 0, active: 0, pending: 0, completed: 0, failed: 0 });
	});
});
