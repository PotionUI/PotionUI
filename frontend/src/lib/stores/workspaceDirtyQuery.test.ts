import { describe, it, expect, vi, afterEach } from 'vitest';
import { get } from 'svelte/store';
import {
	activeWorkspaceDirtyQuery,
	answerWorkspaceDirtyQuery,
	queryTabDirty
} from './workspaceDirtyQuery';

function head() {
	const query = get(activeWorkspaceDirtyQuery);
	if (!query) throw new Error('expected a pending workspace dirty query');
	return query;
}

afterEach(() => {
	const query = get(activeWorkspaceDirtyQuery);
	if (query) answerWorkspaceDirtyQuery(query.id, null);
	vi.useRealTimers();
});

describe('queryTabDirty', () => {
	it('publishes a query for the given tab id', () => {
		void queryTabDirty('tab-a');
		expect(head()).toMatchObject({ tabId: 'tab-a' });
	});

	it('resolves with the answered value', async () => {
		const pending = queryTabDirty('tab-a');
		answerWorkspaceDirtyQuery(head().id, true);
		await expect(pending).resolves.toBe(true);
		expect(get(activeWorkspaceDirtyQuery)).toBeNull();
	});

	it('resolves false when answered false', async () => {
		const pending = queryTabDirty('tab-a');
		answerWorkspaceDirtyQuery(head().id, false);
		await expect(pending).resolves.toBe(false);
	});

	it('resolves exactly once even if answered twice', async () => {
		const pending = queryTabDirty('tab-a');
		const { id } = head();
		answerWorkspaceDirtyQuery(id, true);
		answerWorkspaceDirtyQuery(id, false);
		await expect(pending).resolves.toBe(true);
	});

	it('ignores an answer for an unknown id without disturbing the pending query', async () => {
		const pending = queryTabDirty('tab-a');
		answerWorkspaceDirtyQuery(9999, true);
		expect(head().tabId).toBe('tab-a');
		answerWorkspaceDirtyQuery(head().id, true);
		await expect(pending).resolves.toBe(true);
	});

	it('resolves null on its own after the grace period when nothing answers', async () => {
		vi.useFakeTimers();
		const pending = queryTabDirty('tab-a', 50);
		vi.advanceTimersByTime(51);
		await expect(pending).resolves.toBeNull();
		expect(get(activeWorkspaceDirtyQuery)).toBeNull();
	});
});
