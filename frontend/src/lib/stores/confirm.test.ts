import { describe, it, expect, afterEach } from 'vitest';
import { get } from 'svelte/store';
import {
	activeConfirm,
	cancelAllConfirms,
	confirmDialog,
	settleConfirm
} from './confirm';

afterEach(() => cancelAllConfirms());

function head() {
	const request = get(activeConfirm);
	if (!request) throw new Error('expected a pending confirm request');
	return request;
}

describe('confirmDialog', () => {
	it('resolves true when confirmed', async () => {
		const pending = confirmDialog({ message: 'Delete this?' });
		settleConfirm(head().id, true);
		await expect(pending).resolves.toBe(true);
	});

	it('resolves false when cancelled', async () => {
		const pending = confirmDialog({ message: 'Delete this?' });
		settleConfirm(head().id, false);
		await expect(pending).resolves.toBe(false);
	});

	it('exposes the request with its options', () => {
		confirmDialog({ message: 'Remove tag', title: 'Remove', variant: 'danger' });
		expect(head()).toMatchObject({ message: 'Remove tag', title: 'Remove', variant: 'danger' });
	});

	it('shows one request at a time and queues the rest in order', async () => {
		const first = confirmDialog({ message: 'first' });
		const second = confirmDialog({ message: 'second' });

		expect(head().message).toBe('first');
		settleConfirm(head().id, true);

		expect(head().message).toBe('second');
		settleConfirm(head().id, false);

		expect(get(activeConfirm)).toBeNull();
		await expect(first).resolves.toBe(true);
		await expect(second).resolves.toBe(false);
	});

	it('resolves each queued promise exactly once', async () => {
		const pending = confirmDialog({ message: 'once' });
		const { id } = head();
		settleConfirm(id, true);
		settleConfirm(id, false);
		await expect(pending).resolves.toBe(true);
	});

	it('ignores a settle for an unknown id without disturbing the queue', async () => {
		const pending = confirmDialog({ message: 'still here' });
		settleConfirm(9999, true);
		expect(head().message).toBe('still here');
		settleConfirm(head().id, true);
		await expect(pending).resolves.toBe(true);
	});

	it('leaves no promise dangling when the host unmounts', async () => {
		const first = confirmDialog({ message: 'first' });
		const second = confirmDialog({ message: 'second' });
		cancelAllConfirms();
		expect(get(activeConfirm)).toBeNull();
		await expect(Promise.all([first, second])).resolves.toEqual([false, false]);
	});
});
