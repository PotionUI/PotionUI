import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: { setModelFavorite: vi.fn() }
}));

import { api } from '$lib/services/api/index';
import { toggleModelFavoriteOptimistic } from './modelFavorite';

describe('toggleModelFavoriteOptimistic', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('applies the flip immediately, before the API call resolves', async () => {
		let applied: boolean[] = [];
		(api.setModelFavorite as any).mockImplementation(async () => {
			// The optimistic apply must already have happened by the time the
			// (pending) API call is inspected.
			expect(applied).toEqual([true]);
			return { success: true };
		});
		await toggleModelFavoriteOptimistic({ id: '1', is_favorite: false }, (v) => applied.push(v));
		expect(applied).toEqual([true]);
		expect(api.setModelFavorite).toHaveBeenCalledWith('1', true);
	});

	it('toggles false -> true and true -> false', async () => {
		(api.setModelFavorite as any).mockResolvedValue({ success: true });
		let applied: boolean[] = [];
		await toggleModelFavoriteOptimistic({ id: '1', is_favorite: true }, (v) => applied.push(v));
		expect(applied).toEqual([false]);
	});

	it('reverts via apply when the API reports failure', async () => {
		(api.setModelFavorite as any).mockResolvedValue({ success: false });
		let applied: boolean[] = [];
		await toggleModelFavoriteOptimistic({ id: '1', is_favorite: false }, (v) => applied.push(v));
		expect(applied).toEqual([true, false]);
	});

	it('reverts via apply when the API call throws', async () => {
		(api.setModelFavorite as any).mockRejectedValue(new Error('network error'));
		let applied: boolean[] = [];
		await toggleModelFavoriteOptimistic({ id: '1', is_favorite: false }, (v) => applied.push(v));
		expect(applied).toEqual([true, false]);
	});
});
