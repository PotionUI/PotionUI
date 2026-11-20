import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { editLinkForSave, SAVE_LINK_WAIT_MS } from './saveLink';

beforeEach(() => {
	vi.useFakeTimers();
});

afterEach(() => {
	vi.useRealTimers();
});

describe('editLinkForSave', () => {
	it('returns the link the engine settles on', async () => {
		await expect(editLinkForSave(async () => ({ id: 'ed_1', revision: 4 }))).resolves.toEqual({ id: 'ed_1', revision: 4 });
	});

	it('returns nothing when the tab has no link', async () => {
		await expect(editLinkForSave(async () => null)).resolves.toBeNull();
	});

	it('returns nothing when the flush fails', async () => {
		await expect(editLinkForSave(async () => Promise.reject(new Error('boom')))).resolves.toBeNull();
	});

	it('stops waiting on a flush that hangs', async () => {
		let settled = false;
		const result = editLinkForSave(() => new Promise(() => undefined)).then((link) => {
			settled = true;
			return link;
		});
		await vi.advanceTimersByTimeAsync(SAVE_LINK_WAIT_MS - 1);
		expect(settled).toBe(false);
		await vi.advanceTimersByTimeAsync(1);
		await expect(result).resolves.toBeNull();
	});

	it('does not wait at all for a flush that is quick', async () => {
		const result = editLinkForSave(() => new Promise((resolve) => setTimeout(() => resolve({ id: 'ed_1', revision: 2 }), 200)));
		await vi.advanceTimersByTimeAsync(200);
		await expect(result).resolves.toEqual({ id: 'ed_1', revision: 2 });
		expect(vi.getTimerCount()).toBe(0);
	});
});
