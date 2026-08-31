import { describe, it, expect } from 'vitest';
import {
	transferMatchesFilename,
	findTransferForFilename,
	hasRunningTransfer,
	transferProgressPercent,
	filterSyncRows,
	countByStatus,
	sumSizeBytes
} from './backendModelsSync';
import type { RemoteModelSyncRow, WorkerModelTransfer } from '$lib/services/admin-api';

function transfer(overrides: Partial<WorkerModelTransfer> = {}): WorkerModelTransfer {
	return {
		id: 't1',
		kind: 'upload',
		relative_path: 'checkpoints/model.safetensors',
		total_bytes: 100,
		received_bytes: 50,
		state: 'running',
		error: null,
		...overrides
	};
}

describe('transferMatchesFilename', () => {
	it('matches on the trailing path segment', () => {
		expect(transferMatchesFilename(transfer(), 'model.safetensors')).toBe(true);
	});

	it('does not match a filename that is only a substring', () => {
		expect(transferMatchesFilename(transfer(), 'odel.safetensors')).toBe(false);
	});

	it('does not cross-match a different file sharing a suffix', () => {
		expect(
			transferMatchesFilename(
				transfer({ relative_path: 'checkpoints/barmodel.safetensors' }),
				'model.safetensors'
			)
		).toBe(false);
	});
});

describe('findTransferForFilename', () => {
	it('returns the most recent matching transfer', () => {
		const older = transfer({ id: 'old', state: 'failed' });
		const newer = transfer({ id: 'new', state: 'running' });
		expect(findTransferForFilename([older, newer], 'model.safetensors')?.id).toBe('new');
	});

	it('returns undefined when nothing matches', () => {
		expect(findTransferForFilename([transfer()], 'other.safetensors')).toBeUndefined();
	});
});

describe('hasRunningTransfer', () => {
	it('is true when any transfer is running', () => {
		expect(hasRunningTransfer([transfer({ state: 'completed' }), transfer({ state: 'running' })])).toBe(true);
	});

	it('is false when none are running', () => {
		expect(hasRunningTransfer([transfer({ state: 'completed' }), transfer({ state: 'failed' })])).toBe(false);
	});
});

describe('transferProgressPercent', () => {
	it('computes a rounded percentage', () => {
		expect(transferProgressPercent(transfer({ received_bytes: 33, total_bytes: 100 }))).toBe(33);
	});

	it('clamps at 100', () => {
		expect(transferProgressPercent(transfer({ received_bytes: 150, total_bytes: 100 }))).toBe(100);
	});

	it('is 0 for an unknown total', () => {
		expect(transferProgressPercent(transfer({ total_bytes: 0 }))).toBe(0);
	});
});

function row(overrides: Partial<RemoteModelSyncRow> = {}): RemoteModelSyncRow {
	return {
		model_id: 'm1',
		filename: 'checkpoint.safetensors',
		model_type: 'checkpoints',
		size_bytes: 100,
		status: 'missing',
		providers_can_fetch: true,
		...overrides
	};
}

describe('filterSyncRows', () => {
	const rows = [
		row({ model_id: 'a', filename: 'flux-dev.safetensors', model_type: 'checkpoints', status: 'missing' }),
		row({ model_id: 'b', filename: 'sdxl-base.safetensors', model_type: 'checkpoints', status: 'on_worker' }),
		row({ model_id: 'c', filename: 'my-lora.safetensors', model_type: 'loras', status: 'digest_mismatch' })
	];

	it('matches filename by case-insensitive substring', () => {
		expect(filterSyncRows(rows, { search: 'FLUX', modelType: 'all', status: 'all' }).map((r) => r.model_id)).toEqual(['a']);
	});

	it('filters by model type', () => {
		expect(filterSyncRows(rows, { search: '', modelType: 'loras', status: 'all' }).map((r) => r.model_id)).toEqual(['c']);
	});

	it('filters by status', () => {
		expect(filterSyncRows(rows, { search: '', modelType: 'all', status: 'on_worker' }).map((r) => r.model_id)).toEqual(['b']);
	});

	it('combines search, type, and status filters', () => {
		expect(
			filterSyncRows(rows, { search: 'sdxl', modelType: 'checkpoints', status: 'on_worker' }).map((r) => r.model_id)
		).toEqual(['b']);
	});

	it('returns everything when no filters narrow the set', () => {
		expect(filterSyncRows(rows, { search: '', modelType: 'all', status: 'all' })).toHaveLength(3);
	});
});

describe('countByStatus', () => {
	it('tallies rows per status', () => {
		const rows = [row({ status: 'missing' }), row({ status: 'missing' }), row({ status: 'on_worker' })];
		expect(countByStatus(rows)).toEqual({ missing: 2, on_worker: 1, digest_mismatch: 0 });
	});

	it('is all zero for an empty list', () => {
		expect(countByStatus([])).toEqual({ missing: 0, on_worker: 0, digest_mismatch: 0 });
	});
});

describe('sumSizeBytes', () => {
	it('sums known sizes and treats unknown sizes as zero', () => {
		const rows = [row({ size_bytes: 100 }), row({ size_bytes: null }), row({ size_bytes: 250 })];
		expect(sumSizeBytes(rows)).toBe(350);
	});

	it('is 0 for an empty list', () => {
		expect(sumSizeBytes([])).toBe(0);
	});
});
