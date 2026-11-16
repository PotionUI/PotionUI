import { logger } from '$lib/utils/logger';

export const GUARD_TIMEOUT_MS = 10_000;

const failed = new Set<string>();
const listeners = new Set<() => void>();

export function onGuardFailure(listener: () => void): () => void {
	listeners.add(listener);
	return () => listeners.delete(listener);
}

function isAbort(error: unknown): boolean {
	return (error as { name?: string } | null)?.name === 'AbortError';
}

export function reportFailure(entry: string, what: string, error: unknown): void {
	if (failed.has(entry)) return;
	failed.add(entry);
	logger.error(`Editor entry ${entry} failed in ${what}:`, error);
	queueMicrotask(() => listeners.forEach((listener) => listener()));
}

export function hasFailed(entry: string): boolean {
	return failed.has(entry);
}

export function clearFailure(entry: string): void {
	failed.delete(entry);
}

export function resetFailures(): void {
	failed.clear();
}

export function guard<T>(entry: string, what: string, run: () => T, fallback: T): T {
	try {
		return run();
	} catch (error) {
		reportFailure(entry, what, error);
		return fallback;
	}
}

export async function guardAsync<T>(entry: string, what: string, run: () => Promise<T>, fallback: T, timeoutMs = GUARD_TIMEOUT_MS): Promise<T> {
	let timer: ReturnType<typeof setTimeout> | undefined;
	try {
		const timeout = new Promise<never>((_, reject) => {
			timer = setTimeout(() => reject(new Error(`${what} did not finish in ${timeoutMs} ms`)), timeoutMs);
		});
		return await Promise.race([run(), timeout]);
	} catch (error) {
		if (isAbort(error)) throw error;
		reportFailure(entry, what, error);
		return fallback;
	} finally {
		clearTimeout(timer);
	}
}
