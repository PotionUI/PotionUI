import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

const mockGet = vi.fn();
const mockPut = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: () => ({
			get: (...args: unknown[]) => mockGet(...args),
			put: (...args: unknown[]) => mockPut(...args)
		})
	}
}));

async function freshStore() {
	vi.resetModules();
	return import('./promptSegmentActionPins');
}

describe('stores/promptSegmentActionPins', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockGet.mockResolvedValue({ data: { data: { prompt_segment_pinned_actions: ['duplicate'] } } });
		mockPut.mockResolvedValue({ data: { success: true } });
	});

	it('defaults to no pinned actions before load', async () => {
		const { promptSegmentActionPins } = await freshStore();
		expect(get(promptSegmentActionPins).ids).toEqual([]);
	});

	it('init reads the user preference once from /api/settings', async () => {
		const { promptSegmentActionPins } = await freshStore();
		await promptSegmentActionPins.init();
		await promptSegmentActionPins.init();
		expect(mockGet).toHaveBeenCalledTimes(1);
		expect(mockGet).toHaveBeenCalledWith('/api/settings');
		const state = get(promptSegmentActionPins);
		expect(state.ids).toEqual(['duplicate']);
		expect(state.loaded).toBe(true);
	});

	it('ignores a stored value that is not an array of strings', async () => {
		mockGet.mockResolvedValueOnce({ data: { data: { prompt_segment_pinned_actions: 'duplicate' } } });
		const { promptSegmentActionPins } = await freshStore();
		await promptSegmentActionPins.init();
		expect(get(promptSegmentActionPins).ids).toEqual([]);
	});

	it('togglePin adds an id, then removes it again on a second toggle', async () => {
		const { promptSegmentActionPins } = await freshStore();
		await promptSegmentActionPins.togglePin('editDetails');
		expect(get(promptSegmentActionPins).ids).toEqual(['editDetails']);
		expect(mockPut).toHaveBeenCalledWith('/api/settings/prompt_segment_pinned_actions', {
			value: ['editDetails']
		});

		await promptSegmentActionPins.togglePin('editDetails');
		expect(get(promptSegmentActionPins).ids).toEqual([]);
		expect(mockPut).toHaveBeenCalledWith('/api/settings/prompt_segment_pinned_actions', {
			value: []
		});
	});

	it('togglePin rolls back when the save fails', async () => {
		const { promptSegmentActionPins } = await freshStore();
		mockPut.mockRejectedValueOnce(new Error('offline'));
		await promptSegmentActionPins.togglePin('duplicate');
		expect(get(promptSegmentActionPins).ids).toEqual([]);
	});

	it('reset() drops the loaded pins back to empty, without saving anything', async () => {
		const { promptSegmentActionPins } = await freshStore();
		await promptSegmentActionPins.init();
		expect(get(promptSegmentActionPins).ids).toEqual(['duplicate']);

		promptSegmentActionPins.reset();

		expect(get(promptSegmentActionPins)).toEqual({ ids: [], loaded: false });
		expect(mockPut).not.toHaveBeenCalled();
	});

	it("reset() clears the one-shot init guard, so the next user's init() re-fetches", async () => {
		const { promptSegmentActionPins } = await freshStore();
		await promptSegmentActionPins.init();
		expect(mockGet).toHaveBeenCalledTimes(1);

		promptSegmentActionPins.reset();
		mockGet.mockResolvedValueOnce({ data: { data: { prompt_segment_pinned_actions: ['saveAsSegment'] } } });
		await promptSegmentActionPins.init();

		expect(mockGet).toHaveBeenCalledTimes(2);
		expect(get(promptSegmentActionPins).ids).toEqual(['saveAsSegment']);
	});
});
