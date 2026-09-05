import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
	peekGenerationOutputs,
	setGenerationOutputs,
	retireGeneration,
	isGenerationOutputsRetired,
	setGenerationUnsubscribeHandler,
	retireOrphanedGenerationIds,
	resetGenerationOutputsRetirementForTests
} from './generationOutputs';

function outputs(videoPath: string) {
	return {
		images: [],
		videos: [{ url: videoPath, originalUrl: videoPath } as any],
		audios: [],
		meshes: []
	};
}

describe('retireGeneration', () => {
	beforeEach(() => {
		resetGenerationOutputsRetirementForTests();
		setGenerationUnsubscribeHandler(null);
	});

	it('drops the cache entry and calls the given unsubscribe', () => {
		setGenerationOutputs('gen-1', outputs('/api/media/generations/gen-1/0.mp4'));
		const unsubscribe = vi.fn();

		retireGeneration('gen-1', unsubscribe);

		expect(peekGenerationOutputs('gen-1')).toEqual({ images: [], videos: [], audios: [], meshes: [] });
		expect(unsubscribe).toHaveBeenCalledWith('gen-1');
	});

	it('marks the id retired so a later setGenerationOutputs cannot recreate the entry', () => {
		setGenerationOutputs('gen-2', outputs('/api/media/generations/gen-2/0.mp4'));
		retireGeneration('gen-2', vi.fn());

		expect(isGenerationOutputsRetired('gen-2')).toBe(true);

		// A stray, reordered gallery_update for the same (already-retired) id
		// arriving afterwards must not resurrect a cache entry for it.
		setGenerationOutputs('gen-2', outputs('/api/media/generations/gen-2/1.mp4'));
		expect(peekGenerationOutputs('gen-2')).toEqual({ images: [], videos: [], audios: [], meshes: [] });
	});

	it('is idempotent -- retiring an already-retired id repeats only the unsubscribe call', () => {
		const unsubscribe = vi.fn();
		retireGeneration('gen-3', unsubscribe);
		retireGeneration('gen-3', unsubscribe);

		expect(isGenerationOutputsRetired('gen-3')).toBe(true);
		expect(unsubscribe).toHaveBeenCalledTimes(2);
	});

	it('is a no-op for an undefined id', () => {
		expect(() => retireGeneration(undefined, vi.fn())).not.toThrow();
	});
});

describe('retireOrphanedGenerationIds', () => {
	beforeEach(() => {
		resetGenerationOutputsRetirementForTests();
		setGenerationUnsubscribeHandler(null);
	});

	it('retires every id using the registered unsubscribe handler', () => {
		const unsubscribe = vi.fn();
		setGenerationUnsubscribeHandler(unsubscribe);
		setGenerationOutputs('gen-a', outputs('/a.mp4'));
		setGenerationOutputs('gen-b', outputs('/b.mp4'));

		retireOrphanedGenerationIds(['gen-a', 'gen-b']);

		expect(unsubscribe).toHaveBeenCalledWith('gen-a');
		expect(unsubscribe).toHaveBeenCalledWith('gen-b');
		expect(peekGenerationOutputs('gen-a')).toEqual({ images: [], videos: [], audios: [], meshes: [] });
		expect(isGenerationOutputsRetired('gen-b')).toBe(true);
	});

	it('is a harmless no-op unsubscribe when nothing is registered (still retires the cache)', () => {
		setGenerationOutputs('gen-c', outputs('/c.mp4'));
		expect(() => retireOrphanedGenerationIds(['gen-c'])).not.toThrow();
		expect(isGenerationOutputsRetired('gen-c')).toBe(true);
	});
});

describe('retired id tracking is bounded', () => {
	beforeEach(() => {
		resetGenerationOutputsRetirementForTests();
		setGenerationUnsubscribeHandler(null);
	});

	it('evicts the oldest retired id once the bound is exceeded, instead of growing forever', () => {
		// One past whatever the module's internal limit is (200) -- the first
		// id retired must have been evicted, proving the tracking set does not
		// grow without bound across a long session's worth of generations.
		for (let i = 0; i < 201; i++) {
			retireGeneration(`gen-bound-${i}`, vi.fn());
		}

		expect(isGenerationOutputsRetired('gen-bound-0')).toBe(false);
		expect(isGenerationOutputsRetired('gen-bound-200')).toBe(true);
	});
});
