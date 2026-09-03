// @vitest-environment jsdom
//
// The preset list row's requirements indicator is a pure derivation from
// `requirements_summary` (PresetsTab.svelte's presetTrailing snippet) -
// factored out so its priority rule (a hard miss beats an unknown/optional
// miss beats all-satisfied) and the "never checked" case are covered without
// mounting the whole master-detail preset page.
import { describe, it, expect } from 'vitest';
import { presetRequirementsBadge } from '$lib/utils/presetRequirementsBadge';

describe('presetRequirementsBadge', () => {
	it('shows nothing when the preset has never been checked', () => {
		expect(presetRequirementsBadge(null)).toBeNull();
		expect(presetRequirementsBadge(undefined)).toBeNull();
	});

	it('shows a success ratio chip when everything is satisfied', () => {
		expect(presetRequirementsBadge({ ok: 7, missing: 0, unknown: 0, optional_missing: 0 })).toEqual({
			variant: 'success',
			label: '7/7'
		});
	});

	it('shows a danger "N missing" chip when anything is hard-missing', () => {
		expect(presetRequirementsBadge({ ok: 5, missing: 2, unknown: 0, optional_missing: 0 })).toEqual({
			variant: 'danger',
			label: '2 missing'
		});
	});

	it('shows a warning "N unknown" chip when nothing is missing but something is unchecked', () => {
		expect(presetRequirementsBadge({ ok: 5, missing: 0, unknown: 2, optional_missing: 0 })).toEqual({
			variant: 'warning',
			label: '2 unknown'
		});
	});

	it('shows a warning "N optional" chip when the only gaps are optional misses', () => {
		expect(presetRequirementsBadge({ ok: 5, missing: 0, unknown: 0, optional_missing: 2 })).toEqual({
			variant: 'warning',
			label: '2 optional'
		});
	});

	it('shows a combined warning "N unchecked" chip when both unknown and optional-missing are present', () => {
		expect(presetRequirementsBadge({ ok: 3, missing: 0, unknown: 1, optional_missing: 1 })).toEqual({
			variant: 'warning',
			label: '2 unchecked'
		});
	});

	it('prefers a hard miss over unknown and optional-missing when all three are present', () => {
		expect(presetRequirementsBadge({ ok: 2, missing: 1, unknown: 1, optional_missing: 1 })).toEqual({
			variant: 'danger',
			label: '1 missing'
		});
	});

	it('never lets an optional miss alone read as danger', () => {
		const badge = presetRequirementsBadge({ ok: 4, missing: 0, unknown: 0, optional_missing: 3 });
		expect(badge?.variant).toBe('warning');
	});
});
