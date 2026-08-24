import { describe, expect, it } from 'vitest';
import { validateRegisterFields, validateClaimFields } from './registrationValidation';

const validRegister = { username: 'ada', email: 'ada@example.com', password: 'longenough', confirmPassword: 'longenough' };
const validClaim = { ...validRegister, claimCode: '', claimCodeRequired: false };

describe('validateRegisterFields', () => {
	it('passes valid fields', () => {
		expect(validateRegisterFields(validRegister)).toBeNull();
	});

	it('requires username, email and password', () => {
		expect(validateRegisterFields({ ...validRegister, username: '' })).toBe('Please fill in all fields');
		expect(validateRegisterFields({ ...validRegister, email: '' })).toBe('Please fill in all fields');
		expect(validateRegisterFields({ ...validRegister, password: '' })).toBe('Please fill in all fields');
	});

	it('checks password match before length', () => {
		// Both a mismatch and a too-short password: the original /register form
		// surfaces the mismatch message first.
		expect(validateRegisterFields({ ...validRegister, password: 'short', confirmPassword: 'other' })).toBe(
			'Passwords do not match'
		);
	});

	it('rejects passwords under 8 characters', () => {
		expect(validateRegisterFields({ ...validRegister, password: 'short', confirmPassword: 'short' })).toBe(
			'Password must be at least 8 characters'
		);
	});
});

describe('validateClaimFields', () => {
	it('passes valid fields', () => {
		expect(validateClaimFields(validClaim)).toBeNull();
	});

	it('requires username, email and password, with a trailing period', () => {
		expect(validateClaimFields({ ...validClaim, username: '' })).toBe('Please fill in all fields.');
	});

	it('checks password length before match', () => {
		// Both a mismatch and a too-short password: the original /setup/claim
		// form surfaces the length message first - the opposite order from
		// validateRegisterFields.
		expect(validateClaimFields({ ...validClaim, password: 'short', confirmPassword: 'other' })).toBe(
			'Password must be at least 8 characters.'
		);
	});

	it('rejects mismatched passwords once length passes', () => {
		expect(
			validateClaimFields({ ...validClaim, password: 'longenough', confirmPassword: 'different' })
		).toBe('Passwords do not match.');
	});

	it('requires a claim code only when claimCodeRequired is set', () => {
		expect(validateClaimFields({ ...validClaim, claimCodeRequired: true, claimCode: '' })).toBe(
			'Enter the claim code shown in the terminal where PotionUI was started.'
		);
		expect(validateClaimFields({ ...validClaim, claimCodeRequired: true, claimCode: 'abc123' })).toBeNull();
		expect(validateClaimFields({ ...validClaim, claimCodeRequired: false, claimCode: '' })).toBeNull();
	});
});
