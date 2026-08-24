import { describe, it, expect } from 'vitest';
import { validateAvatarFile, AVATAR_MAX_BYTES } from './avatarValidation';

describe('validateAvatarFile', () => {
	it('accepts allowed image types under the size cap', () => {
		for (const type of ['image/png', 'image/jpeg', 'image/webp', 'image/gif']) {
			expect(validateAvatarFile({ type, size: 1024 })).toBeNull();
		}
	});

	it('rejects a disallowed type', () => {
		const error = validateAvatarFile({ type: 'image/svg+xml', size: 1024 });
		expect(error).not.toBeNull();
		expect(error).toContain('PNG');
	});

	it('rejects a file over 5 MB', () => {
		const error = validateAvatarFile({ type: 'image/png', size: AVATAR_MAX_BYTES + 1 });
		expect(error).not.toBeNull();
		expect(error).toContain('5 MB');
	});

	it('accepts a file exactly at the size cap', () => {
		expect(validateAvatarFile({ type: 'image/png', size: AVATAR_MAX_BYTES })).toBeNull();
	});
});
