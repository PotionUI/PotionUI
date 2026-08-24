// Client-side mirror of the backend's change-password constraints (min 8
// chars, max 72 bytes - bcrypt's limit). The server remains the source of
// truth; this only gives fast, inline feedback before a round trip.

export interface PasswordChangeInput {
	currentPassword: string;
	newPassword: string;
	confirmPassword: string;
}

export interface PasswordChangeErrors {
	current?: string;
	new?: string;
	confirm?: string;
}

export interface PasswordChangeValidation {
	valid: boolean;
	errors: PasswordChangeErrors;
}

export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_BYTES = 72;

function byteLength(value: string): number {
	return new TextEncoder().encode(value).length;
}

export function validatePasswordChange({
	currentPassword,
	newPassword,
	confirmPassword
}: PasswordChangeInput): PasswordChangeValidation {
	const errors: PasswordChangeErrors = {};

	if (!currentPassword) {
		errors.current = 'Enter your current password.';
	}

	if (!newPassword) {
		errors.new = 'Enter a new password.';
	} else if (newPassword.length < PASSWORD_MIN_LENGTH) {
		errors.new = `New password must be at least ${PASSWORD_MIN_LENGTH} characters.`;
	} else if (byteLength(newPassword) > PASSWORD_MAX_BYTES) {
		errors.new = `New password must be at most ${PASSWORD_MAX_BYTES} bytes.`;
	} else if (currentPassword && newPassword === currentPassword) {
		errors.new = 'New password must be different from your current password.';
	}

	if (!confirmPassword) {
		errors.confirm = 'Confirm your new password.';
	} else if (newPassword && confirmPassword !== newPassword) {
		errors.confirm = 'Passwords do not match.';
	}

	return { valid: Object.keys(errors).length === 0, errors };
}
