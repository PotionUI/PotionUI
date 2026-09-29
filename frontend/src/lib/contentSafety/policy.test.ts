import { describe, it, expect } from 'vitest';
import { backfillPercent, isSystemRestrictedGroup, taggerNotice } from './policy';

describe('taggerNotice', () => {
	const missing = { present: false, downloading: false };

	it('is silent when the policy is Allowed or the tagger is present', () => {
		expect(taggerNotice('allowed', missing)).toBeNull();
		expect(taggerNotice('blocked', { present: true, downloading: false })).toBeNull();
		expect(taggerNotice('blocked', null)).toBeNull();
	});

	it('warns that Blocked refuses generations until the model is ready', () => {
		const notice = taggerNotice('blocked', missing);
		expect(notice?.tone).toBe('danger');
		expect(notice?.text).toContain('refuses every generation');
	});

	it('warns that Blur flags conservatively', () => {
		expect(taggerNotice('blur', missing)?.tone).toBe('warning');
	});

	it('explains a download in progress', () => {
		expect(taggerNotice('blocked', { present: false, downloading: true })?.text).toContain('downloading');
	});
});

describe('backfillPercent', () => {
	it('clamps and handles an empty library', () => {
		expect(backfillPercent({ total: 0, rated: 0 })).toBe(100);
		expect(backfillPercent({ total: 200, rated: 50 })).toBe(25);
		expect(backfillPercent({ total: 10, rated: 30 })).toBe(100);
	});
});

describe('isSystemRestrictedGroup', () => {
	it('recognises the flag or the seeded system group', () => {
		expect(isSystemRestrictedGroup({ restricted: true })).toBe(true);
		expect(isSystemRestrictedGroup({ name: 'Restricted content', is_system: true })).toBe(true);
		expect(isSystemRestrictedGroup({ name: 'Restricted content', is_system: false })).toBe(false);
		expect(isSystemRestrictedGroup({ name: 'Kids' })).toBe(false);
	});
});
