import type { LoginProvider } from '$lib/services/api/index';

export function normalizeLoginProviders(raw: unknown): LoginProvider[] {
	if (!Array.isArray(raw)) return [];
	return raw.filter((entry): entry is LoginProvider => isLoginProvider(entry));
}

function isLoginProvider(entry: unknown): entry is LoginProvider {
	if (!entry || typeof entry !== 'object') return false;
	const candidate = entry as Record<string, unknown>;
	return (
		typeof candidate.id === 'string' &&
		typeof candidate.label === 'string' &&
		typeof candidate.start_path === 'string'
	);
}
