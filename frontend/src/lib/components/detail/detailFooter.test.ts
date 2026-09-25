import { describe, expect, it } from 'vitest';
import { detailFooterState } from './detailFooter';

const base = { dirtyCount: 0, mode: 'edit' as const, saving: false, canSave: true };

describe('detailFooterState', () => {
	it('disables Save and Discard when an edit has no changes', () => {
		const state = detailFooterState(base);
		expect(state.saveDisabled).toBe(true);
		expect(state.discardDisabled).toBe(true);
		expect(state.dirtyLabel).toBeNull();
	});

	it('enables both and counts changes once something is dirty', () => {
		const state = detailFooterState({ ...base, dirtyCount: 2 });
		expect(state.saveDisabled).toBe(false);
		expect(state.discardDisabled).toBe(false);
		expect(state.dirtyLabel).toBe('2 unsaved changes');
		expect(detailFooterState({ ...base, dirtyCount: 1 }).dirtyLabel).toBe('1 unsaved change');
	});

	it('lets a create save without a dirty count and labels it Create / Cancel', () => {
		const state = detailFooterState({ ...base, mode: 'create' });
		expect(state.saveDisabled).toBe(false);
		expect(state.discardDisabled).toBe(false);
		expect(state.saveLabel).toBe('Create');
		expect(state.discardLabel).toBe('Cancel');
	});

	it('blocks Save while saving or when the form is invalid', () => {
		expect(detailFooterState({ ...base, dirtyCount: 1, saving: true }).saveDisabled).toBe(true);
		expect(detailFooterState({ ...base, dirtyCount: 1, canSave: false }).saveDisabled).toBe(true);
	});
});
