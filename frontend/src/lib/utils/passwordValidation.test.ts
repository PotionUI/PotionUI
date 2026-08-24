import { describe, it, expect } from 'vitest';
import { validatePasswordChange, PASSWORD_MIN_LENGTH, PASSWORD_MAX_BYTES } from './passwordValidation';

describe('validatePasswordChange', () => {
	it('requires all fields filled', () => {
		const result = validatePasswordChange({
			currentPassword: '',
			newPassword: '',
			confirmPassword: ''
		});
		expect(result.valid).toBe(false);
		expect(result.errors.current).toBeDefined();
		expect(result.errors.new).toBeDefined();
		expect(result.errors.confirm).toBeDefined();
	});

	it('rejects a new password shorter than the minimum', () => {
		const result = validatePasswordChange({
			currentPassword: 'oldpassword',
			newPassword: 'short1',
			confirmPassword: 'short1'
		});
		expect(result.valid).toBe(false);
		expect(result.errors.new).toContain(String(PASSWORD_MIN_LENGTH));
	});

	it('rejects a new password over the max byte length', () => {
		const longPassword = 'a'.repeat(PASSWORD_MAX_BYTES + 1);
		const result = validatePasswordChange({
			currentPassword: 'oldpassword',
			newPassword: longPassword,
			confirmPassword: longPassword
		});
		expect(result.valid).toBe(false);
		expect(result.errors.new).toContain('72');
	});

	it('counts multi-byte characters by their UTF-8 byte length, not code units', () => {
		// Each of these is a 4-byte emoji; 19 of them = 76 bytes, over the 72 cap,
		// even though .length (code units) would read well under a naive 72 cutoff.
		const emojiPassword = '😀'.repeat(19);
		const result = validatePasswordChange({
			currentPassword: 'oldpassword',
			newPassword: emojiPassword,
			confirmPassword: emojiPassword
		});
		expect(result.valid).toBe(false);
		expect(result.errors.new).toContain('72');
	});

	it('rejects a new password identical to the current password', () => {
		const result = validatePasswordChange({
			currentPassword: 'samepassword',
			newPassword: 'samepassword',
			confirmPassword: 'samepassword'
		});
		expect(result.valid).toBe(false);
		expect(result.errors.new).toContain('different');
	});

	it('rejects mismatched confirmation', () => {
		const result = validatePasswordChange({
			currentPassword: 'oldpassword',
			newPassword: 'newpassword1',
			confirmPassword: 'newpassword2'
		});
		expect(result.valid).toBe(false);
		expect(result.errors.confirm).toContain('match');
	});

	it('accepts a valid change', () => {
		const result = validatePasswordChange({
			currentPassword: 'oldpassword',
			newPassword: 'newpassword1',
			confirmPassword: 'newpassword1'
		});
		expect(result.valid).toBe(true);
		expect(result.errors).toEqual({});
	});
});
