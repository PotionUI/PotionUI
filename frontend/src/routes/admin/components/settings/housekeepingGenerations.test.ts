import { describe, it, expect } from 'vitest';
import {
	buildGenerationDeleteParams,
	hasGenerationDeleteCriteria,
	type GenerationDeleteFormState
} from './housekeepingGenerations';

function state(overrides: Partial<GenerationDeleteFormState> = {}): GenerationDeleteFormState {
	return {
		olderThanDays: null,
		withoutMedia: false,
		onlyFailedOrCancelled: false,
		keepFavorites: true,
		...overrides
	};
}

describe('hasGenerationDeleteCriteria', () => {
	it('is false with no criteria at all', () => {
		expect(hasGenerationDeleteCriteria(state())).toBe(false);
	});

	it('keepFavorites alone does not count as a criterion', () => {
		expect(hasGenerationDeleteCriteria(state({ keepFavorites: false }))).toBe(false);
	});

	it('is true once an age limit is set', () => {
		expect(hasGenerationDeleteCriteria(state({ olderThanDays: 30 }))).toBe(true);
	});

	it('is true once without-media is set', () => {
		expect(hasGenerationDeleteCriteria(state({ withoutMedia: true }))).toBe(true);
	});

	it('is true once failed/cancelled-only is set', () => {
		expect(hasGenerationDeleteCriteria(state({ onlyFailedOrCancelled: true }))).toBe(true);
	});
});

describe('buildGenerationDeleteParams', () => {
	it('omits older_than_days and statuses when unset', () => {
		expect(buildGenerationDeleteParams(state())).toEqual({
			without_media: false,
			keep_favorites: true
		});
	});

	it('includes older_than_days when set', () => {
		expect(buildGenerationDeleteParams(state({ olderThanDays: 30 }))).toEqual({
			older_than_days: 30,
			without_media: false,
			keep_favorites: true
		});
	});

	it('maps onlyFailedOrCancelled to a comma-joined statuses filter', () => {
		expect(buildGenerationDeleteParams(state({ onlyFailedOrCancelled: true }))).toEqual({
			without_media: false,
			keep_favorites: true,
			statuses: 'failed,cancelled'
		});
	});

	it('carries withoutMedia and keepFavorites through verbatim', () => {
		expect(
			buildGenerationDeleteParams(state({ withoutMedia: true, keepFavorites: false }))
		).toEqual({
			without_media: true,
			keep_favorites: false
		});
	});
});
