import { describe, it, expect } from 'vitest';
import { canDeleteInspirationComment } from './commentPermissions';

const comment = { user: { id: 'user-1' } };

describe('canDeleteInspirationComment', () => {
	it('allows the comment author', () => {
		expect(canDeleteInspirationComment(comment, { id: 'user-1', account_type: 'USER' })).toBe(
			true
		);
	});

	it('allows an admin who did not write the comment', () => {
		expect(canDeleteInspirationComment(comment, { id: 'user-2', account_type: 'ADMIN' })).toBe(
			true
		);
	});

	it('denies a different non-admin user', () => {
		expect(canDeleteInspirationComment(comment, { id: 'user-2', account_type: 'USER' })).toBe(
			false
		);
	});

	it('denies a signed-out viewer', () => {
		expect(canDeleteInspirationComment(comment, null)).toBe(false);
	});
});
