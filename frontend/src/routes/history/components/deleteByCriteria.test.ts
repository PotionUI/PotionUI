import { describe, expect, it } from 'vitest';
import {
	buildDeleteByCriteriaRequest,
	hasCriteria,
	initialDeleteByCriteriaState,
	FAILED_OR_CANCELLED_STATUSES
} from './deleteByCriteria';

describe('hasCriteria', () => {
	it('is false for the initial (empty) state', () => {
		expect(hasCriteria(initialDeleteByCriteriaState())).toBe(false);
	});

	it('is false when keep_favorites is toggled alone', () => {
		const state = { ...initialDeleteByCriteriaState(), keepFavorites: false };
		expect(hasCriteria(state)).toBe(false);
	});

	it('is true once a tag is selected', () => {
		const state = { ...initialDeleteByCriteriaState(), tagIds: ['tag-1'] };
		expect(hasCriteria(state)).toBe(true);
	});

	it('is false for older_than_days mode with no value yet', () => {
		const state = { ...initialDeleteByCriteriaState(), ageMode: 'older_than_days' as const };
		expect(hasCriteria(state)).toBe(false);
	});

	it('is true once older_than_days has a positive value', () => {
		const state = {
			...initialDeleteByCriteriaState(),
			ageMode: 'older_than_days' as const,
			olderThanDays: 30
		};
		expect(hasCriteria(state)).toBe(true);
	});

	it('is false for date_range mode with neither bound set', () => {
		const state = { ...initialDeleteByCriteriaState(), ageMode: 'date_range' as const };
		expect(hasCriteria(state)).toBe(false);
	});

	it('is true for date_range mode with only a from date', () => {
		const state = {
			...initialDeleteByCriteriaState(),
			ageMode: 'date_range' as const,
			createdFrom: '2026-01-01'
		};
		expect(hasCriteria(state)).toBe(true);
	});

	it('is true when failed/cancelled only is on', () => {
		const state = { ...initialDeleteByCriteriaState(), failedOrCancelledOnly: true };
		expect(hasCriteria(state)).toBe(true);
	});

	it('is true when without media is on', () => {
		const state = { ...initialDeleteByCriteriaState(), withoutMedia: true };
		expect(hasCriteria(state)).toBe(true);
	});
});

describe('buildDeleteByCriteriaRequest', () => {
	it('always sends without_media and keep_favorites', () => {
		const body = buildDeleteByCriteriaRequest(initialDeleteByCriteriaState());
		expect(body).toEqual({ without_media: false, keep_favorites: true });
	});

	it('includes tag_ids only when tags are selected', () => {
		const state = { ...initialDeleteByCriteriaState(), tagIds: ['a', 'b'] };
		expect(buildDeleteByCriteriaRequest(state).tag_ids).toEqual(['a', 'b']);
	});

	it('sends older_than_days only in that age mode with a positive value', () => {
		const state = {
			...initialDeleteByCriteriaState(),
			ageMode: 'older_than_days' as const,
			olderThanDays: 14,
			createdFrom: '2026-01-01'
		};
		const body = buildDeleteByCriteriaRequest(state);
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
		const body = buildDeleteByCriteriaRequest(state);
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
		const body = buildDeleteByCriteriaRequest(state);
		expect(body.created_from).toBe('2026-01-01');
		expect(body.created_to).toBeUndefined();
	});

	it('maps failed/cancelled only to the terminal statuses list', () => {
		const state = { ...initialDeleteByCriteriaState(), failedOrCancelledOnly: true };
		expect(buildDeleteByCriteriaRequest(state).statuses).toEqual(FAILED_OR_CANCELLED_STATUSES);
	});

	it('omits statuses when failed/cancelled only is off', () => {
		expect(buildDeleteByCriteriaRequest(initialDeleteByCriteriaState()).statuses).toBeUndefined();
	});

	it('reflects without_media and keep_favorites verbatim', () => {
		const state = { ...initialDeleteByCriteriaState(), withoutMedia: true, keepFavorites: false };
		const body = buildDeleteByCriteriaRequest(state);
		expect(body.without_media).toBe(true);
		expect(body.keep_favorites).toBe(false);
	});
});
