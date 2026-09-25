export const DEFAULT_PATTERN_MESSAGE = 'Does not match the expected format';

export function patternViolation(
	value: unknown,
	pattern: unknown,
	message?: unknown
): string | null {
	if (typeof value !== 'string' || !value.trim()) return null;
	if (typeof pattern !== 'string' || !pattern) return null;
	let regex: RegExp;
	try {
		regex = new RegExp(pattern, 'm');
	} catch {
		return null;
	}
	if (regex.test(value)) return null;
	return typeof message === 'string' && message ? message : DEFAULT_PATTERN_MESSAGE;
}
