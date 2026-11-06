import { describe, expect, it } from 'vitest';
import { readBatchProgress } from './batchProgress';

describe('readBatchProgress', () => {
	it('names the prompt and its own percent for a prompt batch', () => {
		expect(
			readBatchProgress({ item_index: 3, item_count: 6, item_progress: 0.4, prompt_index: 1, prompt_count: 3 })
		).toEqual({ label: 'Prompt 2 of 3', percent: 40 });
	});

	it('names the item after the preset media kind when there is no prompt batch', () => {
		const progress = { item_index: 1, item_count: 3, item_progress: 0.5 };
		expect(readBatchProgress(progress, 'image')).toEqual({ label: 'Image 2 of 3', percent: 50 });
		expect(readBatchProgress(progress, 'video')?.label).toBe('Video 2 of 3');
		expect(readBatchProgress(progress, 'audio')?.label).toBe('Audio 2 of 3');
	});

	it('uses a neutral noun when the media kind is unknown', () => {
		const progress = { item_index: 1, item_count: 3 };
		expect(readBatchProgress(progress)?.label).toBe('Item 2 of 3');
		expect(readBatchProgress(progress, null)?.label).toBe('Item 2 of 3');
		expect(readBatchProgress(progress, '3d')?.label).toBe('Item 2 of 3');
	});

	it('keeps the label with an unknown percent while the stage has no fraction', () => {
		expect(readBatchProgress({ prompt_index: 0, prompt_count: 2 })).toEqual({ label: 'Prompt 1 of 2', percent: null });
	});

	it('reads nothing for a single image or an absent message', () => {
		expect(readBatchProgress({ item_index: 0, item_count: 1, item_progress: 0.2 })).toBeNull();
		expect(readBatchProgress(null)).toBeNull();
		expect(readBatchProgress({})).toBeNull();
	});

	it('clamps the percent into range', () => {
		expect(readBatchProgress({ prompt_index: 0, prompt_count: 2, item_progress: 1.4 })?.percent).toBe(100);
	});
});
