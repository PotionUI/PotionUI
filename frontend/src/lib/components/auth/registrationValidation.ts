export interface RegisterFields {
	username: string;
	email: string;
	password: string;
	confirmPassword: string;
}

/** Mirrors the original /register form's field order and copy exactly. */
export function validateRegisterFields(fields: RegisterFields): string | null {
	const { username, email, password, confirmPassword } = fields;
	if (!username || !email || !password) return 'Please fill in all fields';
	if (password !== confirmPassword) return 'Passwords do not match';
	if (password.length < 8) return 'Password must be at least 8 characters';
	return null;
}

export interface ClaimFields extends RegisterFields {
	claimCode: string;
	claimCodeRequired: boolean;
}

/**
 * Mirrors the original /setup/claim form's field order and copy exactly -
 * notably the length check runs before the match check here, the opposite
 * order from validateRegisterFields, and every message ends with a period.
 */
export function validateClaimFields(fields: ClaimFields): string | null {
	const { username, email, password, confirmPassword, claimCode, claimCodeRequired } = fields;
	if (!username || !email || !password) return 'Please fill in all fields.';
	if (password.length < 8) return 'Password must be at least 8 characters.';
	if (password !== confirmPassword) return 'Passwords do not match.';
	if (claimCodeRequired && !claimCode) {
		return 'Enter the claim code shown in the terminal where PotionUI was started.';
	}
	return null;
}
