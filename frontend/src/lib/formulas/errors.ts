export function formulaErrorMessage(error: unknown, fallback: string): string {
	const e = error as {
		response?: { data?: { detail?: unknown; error?: unknown; message?: unknown } };
		message?: unknown;
	};
	const data = e?.response?.data;
	const detail = data?.detail;
	if (typeof detail === 'string' && detail) return detail;
	const nested = (detail as { message?: unknown } | undefined)?.message;
	if (typeof nested === 'string' && nested) return nested;
	if (typeof data?.error === 'string' && data.error) return data.error;
	if (typeof data?.message === 'string' && data.message) return data.message;
	return typeof e?.message === 'string' && e.message ? e.message : fallback;
}
