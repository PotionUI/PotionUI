import { describe, it, expect } from 'vitest';
import {
	initialModelDownloadState,
	reduceModelDownloadState,
	shouldContinuePolling
} from './modelDownloadState';

describe('reduceModelDownloadState', () => {
	it('moves through the happy path to completion', () => {
		let state = initialModelDownloadState;
		state = reduceModelDownloadState(state, { type: 'start' });
		expect(state.phase).toBe('starting');

		state = reduceModelDownloadState(state, { type: 'started', downloadId: 'dl-1' });
		expect(state).toEqual({ phase: 'polling', downloadId: 'dl-1', progress: 0, error: null });

		state = reduceModelDownloadState(state, { type: 'poll', status: 'running', progress: 0.4, error: null });
		expect(state.phase).toBe('polling');
		expect(state.progress).toBe(0.4);

		state = reduceModelDownloadState(state, { type: 'poll', status: 'completed', progress: 1, error: null });
		expect(state.phase).toBe('completed');
		expect(state.progress).toBe(1);
		expect(shouldContinuePolling(state.phase)).toBe(false);
	});

	it('surfaces a failed poll with its error', () => {
		let state = reduceModelDownloadState(initialModelDownloadState, { type: 'started', downloadId: 'dl-2' });
		state = reduceModelDownloadState(state, {
			type: 'poll',
			status: 'failed',
			progress: 0.2,
			error: 'disk full'
		});
		expect(state.phase).toBe('failed');
		expect(state.error).toBe('disk full');
	});

	it('marks forbidden without a downloadId', () => {
		const state = reduceModelDownloadState(initialModelDownloadState, { type: 'forbidden' });
		expect(state.phase).toBe('forbidden');
		expect(state.downloadId).toBeNull();
	});

	it('resets back to idle', () => {
		let state = reduceModelDownloadState(initialModelDownloadState, { type: 'started', downloadId: 'dl-3' });
		state = reduceModelDownloadState(state, { type: 'reset' });
		expect(state).toEqual(initialModelDownloadState);
	});

	it('shouldContinuePolling is only true while polling', () => {
		expect(shouldContinuePolling('polling')).toBe(true);
		for (const phase of ['idle', 'starting', 'completed', 'failed', 'forbidden'] as const) {
			expect(shouldContinuePolling(phase)).toBe(false);
		}
	});
});
