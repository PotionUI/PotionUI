export function modelRootsErrorMessage(error: unknown, fallback: string): string {
	const top = (error as { response?: { data?: { message?: unknown } } })?.response?.data?.message;
	if (typeof top === 'string' && top) return top;
	const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
	if (typeof detail === 'object' && detail !== null && 'message' in detail) {
		const message = (detail as { message?: unknown }).message;
		if (typeof message === 'string' && message) return message;
	}
	if (typeof detail === 'string' && detail) return detail;
	const message = (error as { message?: unknown })?.message;
	return typeof message === 'string' && message ? message : fallback;
}

export function modelRootsErrorCode(error: unknown): string | null {
	const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
	if (typeof detail === 'object' && detail !== null && 'error' in detail) {
		const code = (detail as { error?: unknown }).error;
		return typeof code === 'string' ? code : null;
	}
	return null;
}
