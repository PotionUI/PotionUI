import { describe, it, expect } from 'vitest';
import { roleLabel, signedInAgo } from './accountLabels';

const NOW = 1_800_000_000_000;

describe('signedInAgo', () => {
	it('uses minutes, hours and days with the design wording', () => {
		expect(signedInAgo(NOW - 20_000, NOW)).toBe('signed in just now');
		expect(signedInAgo(NOW - 5 * 60_000, NOW)).toBe('signed in 5 m ago');
		expect(signedInAgo(NOW - 9 * 3_600_000, NOW)).toBe('signed in 9 h ago');
		expect(signedInAgo(NOW - 2 * 86_400_000, NOW)).toBe('signed in 2 d ago');
	});
});

describe('roleLabel', () => {
	it('maps the account type to a label', () => {
		expect(roleLabel('ADMIN')).toBe('Admin');
		expect(roleLabel('USER')).toBe('User');
	});
});
