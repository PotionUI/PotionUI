const POLICY_CODES = new Set(['banned_prompt', 'content_blocked', 'content_check_unavailable']);
const ADMIN_ACTION_CODES = new Set(['content_check_unavailable']);

export function isContentPolicyCode(code: string | null | undefined): boolean {
	return !!code && POLICY_CODES.has(code);
}

export function policyShowsErrorId(code: string | null | undefined): boolean {
	return !isContentPolicyCode(code) || ADMIN_ACTION_CODES.has(code as string);
}

export function firstHintLine(userMessage: string | null | undefined, message: string | null | undefined): string {
	const rest = (userMessage ?? '').startsWith(message ?? '') ? (userMessage ?? '').slice((message ?? '').length) : (userMessage ?? '');
	const line = rest
		.split('\n')
		.map((part) => part.replace(/^\s*-\s*/, '').trim())
		.find(Boolean);
	return line ?? '';
}
