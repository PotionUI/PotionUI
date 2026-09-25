import { describe, it, expect } from 'vitest';
import type { Download } from '$lib/stores/downloads';
import { availableDownloadBulkActions } from './downloadBulkActions';

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

describe('availableDownloadBulkActions', () => {
	it('returns no actions when nothing is selected', () => {
		const all = [download({ id: 'a', status: 'failed' })];
		expect(availableDownloadBulkActions(all, new Set())).toEqual(new Set());
	});

	it('offers retry only when a selected row is failed', () => {
		const all = [download({ id: 'a', status: 'failed' }), download({ id: 'b', status: 'completed' })];
		expect(availableDownloadBulkActions(all, new Set(['b']))).toEqual(new Set(['remove']));
		expect(availableDownloadBulkActions(all, new Set(['a']))).toEqual(new Set(['retry', 'remove']));
	});

	it('offers cancel for pending, downloading, and paused selections', () => {
		const all = [
			download({ id: 'a', status: 'pending' }),
			download({ id: 'b', status: 'downloading' }),
			download({ id: 'c', status: 'paused' })
		];
		expect(availableDownloadBulkActions(all, new Set(['a']))).toEqual(new Set(['cancel']));
		expect(availableDownloadBulkActions(all, new Set(['b']))).toEqual(new Set(['cancel']));
		expect(availableDownloadBulkActions(all, new Set(['c']))).toEqual(new Set(['cancel']));
	});

	it('offers remove for completed, failed, and cancelled selections but never for pending or downloading', () => {
		const all = [
			download({ id: 'a', status: 'completed' }),
			download({ id: 'b', status: 'failed' }),
			download({ id: 'c', status: 'cancelled' }),
			download({ id: 'd', status: 'pending' }),
			download({ id: 'e', status: 'downloading' })
		];
		expect(availableDownloadBulkActions(all, new Set(['a']))).toEqual(new Set(['remove']));
		expect(availableDownloadBulkActions(all, new Set(['c']))).toEqual(new Set(['remove']));
		expect(availableDownloadBulkActions(all, new Set(['d', 'e']))).toEqual(new Set(['cancel']));
	});

	it('unions actions across a mixed-status selection', () => {
		const all = [download({ id: 'a', status: 'failed' }), download({ id: 'b', status: 'downloading' })];
		expect(availableDownloadBulkActions(all, new Set(['a', 'b']))).toEqual(
			new Set(['retry', 'remove', 'cancel'])
		);
	});
});
