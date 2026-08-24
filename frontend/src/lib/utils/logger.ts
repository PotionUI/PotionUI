import { isAxiosError } from 'axios';

const isDev = typeof import.meta !== 'undefined' && import.meta.env?.DEV;

export const logger = {
	debug: (...args: unknown[]) => {
		if (isDev) console.log(...args);
	},
	warn: (...args: unknown[]) => {
		if (isDev) console.warn(...args);
	},
	error: (...args: unknown[]) => console.error(...args)
};

/**
 * Extracts a human-readable message from an unknown caught error.
 * Falls back to the provided default message if the error has no message.
 */
export function getErrorMessage(err: unknown, fallback = 'An unexpected error occurred'): string {
	if (err instanceof Error) return err.message;
	if (typeof err === 'string') return err;
	if (
		err !== null &&
		typeof err === 'object' &&
		'message' in err &&
		typeof (err as Record<string, unknown>).message === 'string'
	) {
		return (err as Record<string, unknown>).message as string;
	}
	return fallback;
}

/**
 * Extracts a human-readable message from an unknown caught error, preferring
 * the FastAPI `detail: { message }` shape over axios's own error message.
 */
export function getApiErrorMessage(err: unknown, fallback = 'An unexpected error occurred'): string {
	if (isAxiosError<{ message?: string; detail?: { message?: string } }>(err)) {
		return err.response?.data?.detail?.message || err.response?.data?.message || err.message || fallback;
	}
	return getErrorMessage(err, fallback);
}
