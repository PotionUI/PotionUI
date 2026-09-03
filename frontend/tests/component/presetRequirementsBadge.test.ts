// @vitest-environment jsdom
//
// The preset list row's requirements indicator is a pure derivation from
// `requirements_summary` (PresetsTab.svelte's presetTrailing snippet) -
// factored out so its priority rule (missing beats unknown beats
// all-satisfied) and the "never checked" case are covered without mounting
// the whole master-detail preset page.
import { describe, it, expect } from 'vitest';
import { presetRequirementsBadge } from '$lib/utils/presetRequirementsBadge';

describe('presetRequirementsBadge', () => {
	it('shows nothing when the preset has never been checked', () => {
		expect(presetRequirementsBadge(null)).toBeNull();
		expect(presetRequirementsBadge(undefined)).toBeNull();
	});

	it('shows a success ratio chip when everything is satisfied', () => {
		expect(presetRequirementsBadge({ ok: 7, missing: 0, unknown: 0 })).toEqual({
			variant: 'success',
			label: '7/7'
		});
	});

	it('shows a danger "N missing" chip when anything is missing', () => {
		expect(presetRequirementsBadge({ ok: 5, missing: 2, unknown: 0 })).toEqual({
			variant: 'danger',
			label: '2 missing'
		});
	});

	it('shows a warning "N unknown" chip when nothing is missing but something is unchecked', () => {
		expect(presetRequirementsBadge({ ok: 5, missing: 0, unknown: 2 })).toEqual({
			variant: 'warning',
			label: '2 unknown'
		});
	});

	it('prefers missing over unknown when both are present', () => {
		expect(presetRequirementsBadge({ ok: 3, missing: 1, unknown: 1 })).toEqual({
			variant: 'danger',
			label: '1 missing'
		});
	});
});
