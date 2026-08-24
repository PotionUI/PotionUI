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

// The store is a module-level singleton (init() runs only once per import), so
// each test gets its own fresh module instance rather than sharing state.
async function freshStore() {
	vi.resetModules();
	return import('./nsfwFilter');
}

describe('stores/nsfwFilter', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockGet.mockResolvedValue({
			data: { success: true, data: { media_nsfw_filter_mode: 'show' } }
		});
		mockPut.mockResolvedValue({ data: { success: true } });
	});

	it('defaults to blur before load', async () => {
		const { nsfwFilterStore } = await freshStore();
		expect(get(nsfwFilterStore).mode).toBe('blur');
	});

	it('init reads the user preference once from /api/settings', async () => {
		const { nsfwFilterStore } = await freshStore();
		await nsfwFilterStore.init();
		await nsfwFilterStore.init();
		expect(mockGet).toHaveBeenCalledTimes(1);
		expect(mockGet).toHaveBeenCalledWith('/api/settings');
		const state = get(nsfwFilterStore);
		expect(state.mode).toBe('show');
		expect(state.loaded).toBe(true);
	});

	it('ignores an unrecognized stored value', async () => {
		mockGet.mockResolvedValueOnce({
			data: { success: true, data: { media_nsfw_filter_mode: 'nonsense' } }
		});
		const { nsfwFilterStore } = await freshStore();
		await nsfwFilterStore.init();
		expect(get(nsfwFilterStore).mode).toBe('blur');
	});

	it('setMode writes the per-user setting key', async () => {
		const { nsfwFilterStore } = await freshStore();
		await nsfwFilterStore.setMode('hide');
		expect(mockPut).toHaveBeenCalledWith('/api/settings/media_nsfw_filter_mode', {
			value: 'hide'
		});
		expect(get(nsfwFilterStore).mode).toBe('hide');
	});

	it('setMode rolls back when the save fails', async () => {
		const { nsfwFilterStore } = await freshStore();
		const before = get(nsfwFilterStore).mode;
		mockPut.mockRejectedValueOnce(new Error('offline'));
		await nsfwFilterStore.setMode('hide');
		expect(get(nsfwFilterStore).mode).toBe(before);
	});

	it('reset() drops the loaded preference back to unloaded, without saving anything', async () => {
		const { nsfwFilterStore } = await freshStore();
		await nsfwFilterStore.init();
		expect(get(nsfwFilterStore).mode).toBe('show');

		nsfwFilterStore.reset();

		expect(get(nsfwFilterStore)).toEqual({ mode: 'blur', loaded: false });
		expect(mockPut).not.toHaveBeenCalled();
	});

	it('reset() clears the one-shot init guard, so the next user\'s init() re-fetches', async () => {
		const { nsfwFilterStore } = await freshStore();
		await nsfwFilterStore.init();
		expect(mockGet).toHaveBeenCalledTimes(1);

		nsfwFilterStore.reset();
		mockGet.mockResolvedValueOnce({
			data: { success: true, data: { media_nsfw_filter_mode: 'hide' } }
		});
		await nsfwFilterStore.init();

		expect(mockGet).toHaveBeenCalledTimes(2);
		expect(get(nsfwFilterStore).mode).toBe('hide');
	});
});

describe('nsfwFilter helpers', () => {
	const files = [
		{ id: 1, is_final: true, file_type: 'image', nsfw: false },
		{ id: 2, is_final: true, file_type: 'image', nsfw: true }
	];

	it('visibleMediaFiles passes everything through outside hide mode', async () => {
		const { visibleMediaFiles } = await freshStore();
		expect(visibleMediaFiles(files, 'blur')).toEqual(files);
		expect(visibleMediaFiles(files, 'show')).toEqual(files);
	});

	it('visibleMediaFiles drops nsfw files in hide mode', async () => {
		const { visibleMediaFiles } = await freshStore();
		expect(visibleMediaFiles(files, 'hide')).toEqual([files[0]]);
	});

	it('isGenerationHiddenByNsfw is false unless every file is nsfw in hide mode', async () => {
		const { isGenerationHiddenByNsfw } = await freshStore();
		expect(isGenerationHiddenByNsfw(files, 'hide')).toBe(false);
		expect(isGenerationHiddenByNsfw(files, 'blur')).toBe(false);
		expect(isGenerationHiddenByNsfw([files[1]], 'hide')).toBe(true);
	});

	it('isGenerationHiddenByNsfw is false for a generation with no media yet', async () => {
		const { isGenerationHiddenByNsfw } = await freshStore();
		expect(isGenerationHiddenByNsfw([], 'hide')).toBe(false);
	});

	it('selectableMediaFiles keeps only final image/video files', async () => {
		const { selectableMediaFiles } = await freshStore();
		const mixed = [
			{ id: 1, is_final: true, file_type: 'image' },
			{ id: 2, is_final: false, file_type: 'image' },
			{ id: 3, is_final: true, file_type: 'audio' },
			{ id: 4, is_final: true, file_type: 'VIDEO' }
		];
		expect(selectableMediaFiles(mixed)).toEqual([mixed[0], mixed[3]]);
	});
});
