import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

const openElixirTab = vi.fn();
vi.mock('./tab', () => ({ openElixirTab: (...args: unknown[]) => openElixirTab(...args) }));

import { toasts } from '$lib/stores/toast';
import { blockingRunsOf, brewRequest, requestBrew, showDeleteError } from './openBrew';

function rejection(detail: unknown) {
	return { response: { data: { detail } }, message: 'Request failed' };
}

const blocking = { elixir_id: 'e1', elixir_name: 'Portrait', run_id: 'r41', number: 41 };

beforeEach(() => {
	openElixirTab.mockReset();
	brewRequest.set(null);
	for (const toast of get(toasts)) toasts.remove(toast.id);
});

describe('blockingRunsOf', () => {
	it('reads blocking_runs from the 400 detail', () => {
		expect(blockingRunsOf(rejection({ error: 'x', message: 'm', blocking_runs: [blocking] }))).toEqual([blocking]);
	});

	it('ignores malformed entries and other errors', () => {
		expect(blockingRunsOf(rejection({ blocking_runs: [{ run_id: 1 }, null] }))).toEqual([]);
		expect(blockingRunsOf(rejection('plain'))).toEqual([]);
		expect(blockingRunsOf(new Error('boom'))).toEqual([]);
	});
});

describe('showDeleteError', () => {
	it('keeps a plain error toast when nothing blocks the delete', () => {
		showDeleteError(rejection({ message: 'Nope' }));
		const [toast] = get(toasts);
		expect(toast.message).toBe('Nope');
		expect(toast.action).toBeUndefined();
	});

	it('shows the server message with an Open brew action that opens that elixir on that brew', () => {
		showDeleteError(rejection({ message: 'Brew 41 is still running.', blocking_runs: [blocking, { ...blocking, run_id: 'r40', number: 40 }] }));
		const [toast] = get(toasts);
		expect(toast.message).toBe('Brew 41 is still running.');
		expect(toast.duration).toBe(0);
		expect(toast.action?.label).toBe('Open brew');
		toast.action!.onClick();
		expect(openElixirTab).toHaveBeenCalledWith({ id: 'e1', name: 'Portrait' });
		expect(get(brewRequest)).toEqual({ elixirId: 'e1', runId: 'r41', number: 41 });
	});
});

describe('requestBrew', () => {
	it('publishes the request for the editor to pick up', () => {
		requestBrew(blocking);
		expect(get(brewRequest)?.number).toBe(41);
	});
});
