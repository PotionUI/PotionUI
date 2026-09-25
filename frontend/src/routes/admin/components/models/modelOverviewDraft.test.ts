import { describe, expect, it } from 'vitest';
import { overviewDraftFromModel, overviewDraftIsDirty } from './modelOverviewDraft';

describe('overviewDraftFromModel', () => {
	it('reads description and prompting guidance off the model', () => {
		expect(overviewDraftFromModel({ description: 'Desc', prompting_guidance: 'Guidance' })).toEqual({
			description: 'Desc',
			promptingGuidance: 'Guidance'
		});
	});

	it('defaults both fields to an empty string when unset or the model is missing', () => {
		expect(overviewDraftFromModel({})).toEqual({ description: '', promptingGuidance: '' });
		expect(overviewDraftFromModel(null)).toEqual({ description: '', promptingGuidance: '' });
	});
});

describe('overviewDraftIsDirty', () => {
	const snapshot = { description: 'Desc', promptingGuidance: 'Guidance' };

	it('is false when the draft matches the snapshot', () => {
		expect(overviewDraftIsDirty({ ...snapshot }, snapshot)).toBe(false);
	});

	it('is true when the description changed', () => {
		expect(overviewDraftIsDirty({ ...snapshot, description: 'New' }, snapshot)).toBe(true);
	});

	it('is true when the prompting guidance changed', () => {
		expect(overviewDraftIsDirty({ ...snapshot, promptingGuidance: 'New' }, snapshot)).toBe(true);
	});
});
