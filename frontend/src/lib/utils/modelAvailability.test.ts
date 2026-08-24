import { describe, it, expect } from 'vitest';
import { confidenceDisplay, digestConflictTooltip, isAvailabilityKnown } from './modelAvailability';

describe('confidenceDisplay', () => {
	it('maps the non-problem confidence levels to non-alarming variants', () => {
		expect(confidenceDisplay('verified')).toEqual({ label: 'Verified', variant: 'success' });
		expect(confidenceDisplay('reported')).toEqual({ label: 'Reported', variant: 'info' });
		expect(confidenceDisplay('name_only')).toEqual({ label: 'Name only', variant: 'neutral' });
	});

	it('maps conflict to the danger variant - the backend cannot be used for this model', () => {
		expect(confidenceDisplay('conflict')).toEqual({ label: 'Conflict', variant: 'danger' });
	});

	it('falls back gracefully for missing or unrecognized values', () => {
		expect(confidenceDisplay(null)).toEqual({ label: 'Unknown', variant: 'neutral' });
		expect(confidenceDisplay(undefined)).toEqual({ label: 'Unknown', variant: 'neutral' });
		expect(confidenceDisplay('something_new')).toEqual({ label: 'Unknown', variant: 'neutral' });
	});
});

describe('digestConflictTooltip', () => {
	it('names the required action even when neither digest is known', () => {
		expect(digestConflictTooltip(null, null)).toBe(
			"This backend's copy does not match the expected file. Re-sync or replace the file on this backend, then re-index it."
		);
	});

	it('includes truncated expected/found digests when both are known', () => {
		const found = 'a'.repeat(64);
		const expected = 'b'.repeat(64);
		expect(digestConflictTooltip(found, expected)).toBe(
			"This backend's copy does not match the expected file. Expected " +
				'b'.repeat(12) +
				'..., found ' +
				'a'.repeat(12) +
				"... Re-sync or replace the file on this backend, then re-index it."
		);
	});

	it('omits the digest detail when only one side is known', () => {
		expect(digestConflictTooltip('a'.repeat(64), null)).toBe(
			"This backend's copy does not match the expected file. Re-sync or replace the file on this backend, then re-index it."
		);
	});
});

describe('isAvailabilityKnown', () => {
	it('is unknown when nothing has been indexed and backend_ids is empty', () => {
		expect(isAvailabilityKnown([], false)).toBe(false);
		expect(isAvailabilityKnown(undefined, false)).toBe(false);
	});

	it('is known once indexed, even with an empty backend_ids (no backend can load it)', () => {
		expect(isAvailabilityKnown([], true)).toBe(true);
	});

	it('is known whenever at least one backend is listed, regardless of the indexed flag', () => {
		expect(isAvailabilityKnown(['backend-1'], false)).toBe(true);
		expect(isAvailabilityKnown(['backend-1'], true)).toBe(true);
	});
});
