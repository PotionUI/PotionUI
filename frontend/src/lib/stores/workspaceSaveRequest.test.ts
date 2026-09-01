import { describe, it, expect, vi, afterEach } from 'vitest';
import { get } from 'svelte/store';
import {
	activeWorkspaceSaveRequest,
	requestTabSave,
	settleWorkspaceSaveRequest
} from './workspaceSaveRequest';

function head() {
	const request = get(activeWorkspaceSaveRequest);
	if (!request) throw new Error('expected a pending workspace save request');
	return request;
}

afterEach(() => {
	// Drain any still-pending request so tests don't leak across files.
	const request = get(activeWorkspaceSaveRequest);
	if (request) settleWorkspaceSaveRequest(request.id, false);
	vi.useRealTimers();
});

describe('requestTabSave', () => {
	it('publishes a request for the given tab id', () => {
		void requestTabSave('tab-a');
		expect(head()).toMatchObject({ tabId: 'tab-a' });
	});

	it('resolves true when settled with true', async () => {
		const pending = requestTabSave('tab-a');
		settleWorkspaceSaveRequest(head().id, true);
		await expect(pending).resolves.toBe(true);
		expect(get(activeWorkspaceSaveRequest)).toBeNull();
	});

	it('resolves false when settled with false (save failed)', async () => {
		const pending = requestTabSave('tab-a');
		settleWorkspaceSaveRequest(head().id, false);
		await expect(pending).resolves.toBe(false);
	});

	it('resolves exactly once even if settled twice', async () => {
		const pending = requestTabSave('tab-a');
		const { id } = head();
		settleWorkspaceSaveRequest(id, true);
		settleWorkspaceSaveRequest(id, false);
		await expect(pending).resolves.toBe(true);
	});

	it('ignores a settle for an unknown id without disturbing the pending request', async () => {
		const pending = requestTabSave('tab-a');
		settleWorkspaceSaveRequest(9999, true);
		expect(head().tabId).toBe('tab-a');
		settleWorkspaceSaveRequest(head().id, true);
		await expect(pending).resolves.toBe(true);
	});

	it('resolves true on its own after the grace period when nothing is listening', async () => {
		vi.useFakeTimers();
		const pending = requestTabSave('tab-a', 50);
		vi.advanceTimersByTime(51);
		await expect(pending).resolves.toBe(true);
		expect(get(activeWorkspaceSaveRequest)).toBeNull();
	});
});
