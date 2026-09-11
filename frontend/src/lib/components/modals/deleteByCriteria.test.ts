import { describe, expect, it } from 'vitest';
import {
	buildDeleteByCriteriaRequest,
	hasCriteria,
	initialDeleteByCriteriaState,
	FAILED_OR_CANCELLED_STATUSES,
	type DeleteByCriteriaCriteriaConfig
} from './deleteByCriteria';

// History's original criteria set - keeps the exact pre-generalization
// behaviour (tags/age/status/without-media/keep-favorites, no media type).
const HISTORY_CONFIG: DeleteByCriteriaCriteriaConfig = {
	tags: true,
	age: true,
	status: true,
	withoutMedia: true,
	keepFavorites: true
};

// Library's criteria set - tags/age/media type, no status/without-media/
// keep-favorites (the library has no such concepts).
const LIBRARY_CONFIG: DeleteByCriteriaCriteriaConfig = {
	tags: true,
	age: true,
	mediaType: true
};

describe('hasCriteria (History config)', () => {
	it('is false for the initial (empty) state', () => {
		expect(hasCriteria(initialDeleteByCriteriaState(), HISTORY_CONFIG)).toBe(false);
	});

	it('is false when keep_favorites is toggled alone', () => {
		const state = { ...initialDeleteByCriteriaState(), keepFavorites: false };
		expect(hasCriteria(state, HISTORY_CONFIG)).toBe(false);
	});

	it('is true once a tag is selected', () => {
		const state = { ...initialDeleteByCriteriaState(), tagIds: ['tag-1'] };
		expect(hasCriteria(state, HISTORY_CONFIG)).toBe(true);
	});

	it('is false for older_than_days mode with no value yet', () => {
		const state = { ...initialDeleteByCriteriaState(), ageMode: 'older_than_days' as const };
		expect(hasCriteria(state, HISTORY_CONFIG)).toBe(false);
	});

	it('is true once older_than_days has a positive value', () => {
		const state = {
			...initialDeleteByCriteriaState(),
			ageMode: 'older_than_days' as const,
			olderThanDays: 30
		};
		expect(hasCriteria(state, HISTORY_CONFIG)).toBe(true);
	});

	it('is false for date_range mode with neither bound set', () => {
		const state = { ...initialDeleteByCriteriaState(), ageMode: 'date_range' as const };
		expect(hasCriteria(state, HISTORY_CONFIG)).toBe(false);
	});

	it('is true for date_range mode with only a from date', () => {
		const state = {
			...initialDeleteByCriteriaState(),
			ageMode: 'date_range' as const,
			createdFrom: '2026-01-01'
		};
		expect(hasCriteria(state, HISTORY_CONFIG)).toBe(true);
	});

	it('is true when failed/cancelled only is on', () => {
		const state = { ...initialDeleteByCriteriaState(), failedOrCancelledOnly: true };
		expect(hasCriteria(state, HISTORY_CONFIG)).toBe(true);
	});

	it('is true when without media is on', () => {
		const state = { ...initialDeleteByCriteriaState(), withoutMedia: true };
		expect(hasCriteria(state, HISTORY_CONFIG)).toBe(true);
	});

	it('ignores media type - History does not enable that criterion', () => {
		const state = { ...initialDeleteByCriteriaState(), mediaType: 'video' };
		expect(hasCriteria(state, HISTORY_CONFIG)).toBe(false);
	});
});

describe('buildDeleteByCriteriaRequest (History config)', () => {
	it('always sends without_media and keep_favorites', () => {
		const body = buildDeleteByCriteriaRequest(initialDeleteByCriteriaState(), HISTORY_CONFIG);
		expect(body).toEqual({ without_media: false, keep_favorites: true });
	});

	it('includes tag_ids only when tags are selected', () => {
		const state = { ...initialDeleteByCriteriaState(), tagIds: ['a', 'b'] };
		expect(buildDeleteByCriteriaRequest(state, HISTORY_CONFIG).tag_ids).toEqual(['a', 'b']);
	});

	it('sends older_than_days only in that age mode with a positive value', () => {
		const state = {
			...initialDeleteByCriteriaState(),
			ageMode: 'older_than_days' as const,
			olderThanDays: 14,
			createdFrom: '2026-01-01'
		};
		const body = buildDeleteByCriteriaRequest(state, HISTORY_CONFIG);
		expect(body.older_than_days).toBe(14);
		expect(body.created_from).toBeUndefined();
	});

	it('sends created_from/created_to only in date_range mode', () => {
		const state = {
			...initialDeleteByCriteriaState(),
			ageMode: 'date_range' as const,
			createdFrom: '2026-01-01',
			createdTo: '2026-02-01',
			olderThanDays: 14
		};
		const body = buildDeleteByCriteriaRequest(state, HISTORY_CONFIG);
		expect(body.created_from).toBe('2026-01-01');
		expect(body.created_to).toBe('2026-02-01');
		expect(body.older_than_days).toBeUndefined();
	});

	it('omits an unset date bound', () => {
		const state = {
			...initialDeleteByCriteriaState(),
			ageMode: 'date_range' as const,
			createdFrom: '2026-01-01'
		};
		const body = buildDeleteByCriteriaRequest(state, HISTORY_CONFIG);
		expect(body.created_from).toBe('2026-01-01');
		expect(body.created_to).toBeUndefined();
	});

	it('maps failed/cancelled only to the terminal statuses list', () => {
		const state = { ...initialDeleteByCriteriaState(), failedOrCancelledOnly: true };
		expect(buildDeleteByCriteriaRequest(state, HISTORY_CONFIG).statuses).toEqual(
			FAILED_OR_CANCELLED_STATUSES
		);
	});

	it('omits statuses when failed/cancelled only is off', () => {
		expect(
			buildDeleteByCriteriaRequest(initialDeleteByCriteriaState(), HISTORY_CONFIG).statuses
		).toBeUndefined();
	});

	it('reflects without_media and keep_favorites verbatim', () => {
		const state = { ...initialDeleteByCriteriaState(), withoutMedia: true, keepFavorites: false };
		const body = buildDeleteByCriteriaRequest(state, HISTORY_CONFIG);
		expect(body.without_media).toBe(true);
		expect(body.keep_favorites).toBe(false);
	});

	it('never sends media_type - History does not enable that criterion', () => {
		const state = { ...initialDeleteByCriteriaState(), mediaType: 'video' };
		expect(buildDeleteByCriteriaRequest(state, HISTORY_CONFIG).media_type).toBeUndefined();
	});
});

describe('hasCriteria (Library config)', () => {
	it('is false for the initial (empty) state', () => {
		expect(hasCriteria(initialDeleteByCriteriaState(), LIBRARY_CONFIG)).toBe(false);
	});

	it('is true once a tag is selected', () => {
		const state = { ...initialDeleteByCriteriaState(), tagIds: ['tag-1'] };
		expect(hasCriteria(state, LIBRARY_CONFIG)).toBe(true);
	});

	it('is true once older_than_days has a positive value', () => {
		const state = {
			...initialDeleteByCriteriaState(),
			ageMode: 'older_than_days' as const,
			olderThanDays: 30
		};
		expect(hasCriteria(state, LIBRARY_CONFIG)).toBe(true);
	});

	it('is true once a media type is selected', () => {
		const state = { ...initialDeleteByCriteriaState(), mediaType: 'video' };
		expect(hasCriteria(state, LIBRARY_CONFIG)).toBe(true);
	});

	it('ignores failed/cancelled only and without-media - Library does not enable those', () => {
		const state = {
			...initialDeleteByCriteriaState(),
			failedOrCancelledOnly: true,
			withoutMedia: true
		};
		expect(hasCriteria(state, LIBRARY_CONFIG)).toBe(false);
	});
});

describe('buildDeleteByCriteriaRequest (Library config)', () => {
	it('sends an empty body for the initial (empty) state', () => {
		expect(buildDeleteByCriteriaRequest(initialDeleteByCriteriaState(), LIBRARY_CONFIG)).toEqual({});
	});

	it('never sends without_media, statuses or keep_favorites - Library does not enable those', () => {
		const state = {
			...initialDeleteByCriteriaState(),
			failedOrCancelledOnly: true,
			withoutMedia: true,
			keepFavorites: false
		};
		const body = buildDeleteByCriteriaRequest(state, LIBRARY_CONFIG);
		expect(body.without_media).toBeUndefined();
		expect(body.statuses).toBeUndefined();
		expect(body.keep_favorites).toBeUndefined();
	});

	it('includes tag_ids only when tags are selected', () => {
		const state = { ...initialDeleteByCriteriaState(), tagIds: ['a', 'b'] };
		expect(buildDeleteByCriteriaRequest(state, LIBRARY_CONFIG).tag_ids).toEqual(['a', 'b']);
	});

	it('includes media_type only when set', () => {
		const state = { ...initialDeleteByCriteriaState(), mediaType: 'video' };
		expect(buildDeleteByCriteriaRequest(state, LIBRARY_CONFIG).media_type).toBe('video');
	});

	it('omits media_type when unset', () => {
		expect(
			buildDeleteByCriteriaRequest(initialDeleteByCriteriaState(), LIBRARY_CONFIG).media_type
		).toBeUndefined();
	});
});
