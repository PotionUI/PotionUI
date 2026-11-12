interface ErrorBody {
	message?: unknown;
	error?: unknown;
	detail?: unknown;
	suggestion?: unknown;
}

function bodyOf(source: unknown): ErrorBody | null {
	if (!source || typeof source !== 'object') return null;
	const record = source as Record<string, unknown>;
	const data = (record.response as { data?: unknown } | undefined)?.data ?? record;
	if (!data || typeof data !== 'object') return null;
	const detail = (data as ErrorBody).detail;
	if (detail && typeof detail === 'object') return detail as ErrorBody;
	return data as ErrorBody;
}

export function kitErrorMessage(source: unknown, fallback: string): string {
	const body = bodyOf(source);
	const detail = body?.detail;
	for (const candidate of [body?.message, typeof detail === 'string' ? detail : null, body?.error]) {
		if (typeof candidate === 'string' && candidate.trim() && !/^[a-z_]+$/.test(candidate.trim())) return candidate.trim();
	}
	return fallback;
}

export function kitErrorCode(source: unknown): string | null {
	const code = bodyOf(source)?.error;
	return typeof code === 'string' && code ? code : null;
}

export function kitErrorSuggestion(source: unknown): string | null {
	const suggestion = bodyOf(source)?.suggestion;
	return typeof suggestion === 'string' && suggestion ? suggestion : null;
}

export function isNameClash(source: unknown): boolean {
	const status = (source as { response?: { status?: number } } | null)?.response?.status;
	if (status === 409) return true;
	return /_path_taken$/.test(kitErrorCode(source) ?? '');
}
