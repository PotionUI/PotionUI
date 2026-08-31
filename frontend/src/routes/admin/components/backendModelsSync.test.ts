import { describe, it, expect } from 'vitest';
import {
	transferMatchesFilename,
	findTransferForFilename,
	hasRunningTransfer,
	transferProgressPercent
} from './backendModelsSync';
import type { WorkerModelTransfer } from '$lib/services/admin-api';

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
