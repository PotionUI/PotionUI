import { describe, it, expect } from 'vitest';
import { adminBlockedByExpiry } from './apiKeyDialog';
import { describeEvent } from './apiKeyEvents';
import { formatKeyDate, keyBadge, usedPhrase } from './apiKeyView';
import { makeKey, NOW } from './fixtures';

const FORMATS = [
	['microseconds with offset', (iso: string) => iso.replace('Z', '.123456+00:00')],
	['legacy Z', (iso: string) => iso]
] as const;

describe.each(FORMATS)('key timestamps as %s', (_name, shape) => {
	it('reads last use at day granularity', () => {
		expect(usedPhrase(shape('2026-11-11T08:00:00Z'), NOW)).toBe('used today');
		expect(usedPhrase(shape('2026-11-10T08:00:00Z'), NOW)).toBe('used yesterday');
		expect(usedPhrase(shape('2026-11-08T08:00:00Z'), NOW)).toBe('used 3 days ago');
	});

	it('badges a key about to expire', () => {
		expect(keyBadge(makeKey({ expires_at: shape('2026-11-17T06:00:00Z') }), NOW)).toEqual({ variant: 'warning', label: 'Expires in 6 days' });
	});

	it('formats dates and event dates', () => {
		expect(formatKeyDate(shape('2026-10-14T09:00:00Z'), NOW)).toBe('Oct 14');
		const event = { action: 'created', actor: null, detail: {}, changes: null, created_at: shape('2026-10-22T09:00:00Z') };
		expect(describeEvent(event, NOW).when).toBe('Oct 22');
	});

	it('applies the 90 day rule for administration', () => {
		expect(adminBlockedByExpiry(shape('2026-12-01T00:00:00Z'), NOW)).toBe(false);
		expect(adminBlockedByExpiry(shape('2027-06-01T00:00:00Z'), NOW)).toBe(true);
	});
});
