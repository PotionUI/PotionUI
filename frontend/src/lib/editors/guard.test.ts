import { beforeEach, describe, expect, it, vi } from 'vitest';
import { clearFailure, guard, guardAsync, hasFailed, onGuardFailure, resetFailures } from './guard';

vi.mock('$lib/utils/logger', () => ({ logger: { error: vi.fn(), warn: vi.fn(), debug: vi.fn() } }));

import { logger } from '$lib/utils/logger';

beforeEach(() => {
	resetFailures();
	vi.mocked(logger.error).mockClear();
});

describe('guard', () => {
	it('returns the result when nothing throws', () => {
		expect(guard('image:a', 'summary', () => 7, 0)).toBe(7);
		expect(logger.error).not.toHaveBeenCalled();
	});

	it('swallows a throw, returns the fallback and logs once per entry', () => {
		const boom = () => {
			throw new Error('boom');
		};
		expect(guard('image:a', 'summary', boom, 'fallback')).toBe('fallback');
		expect(guard('image:a', 'payload', boom, 'fallback')).toBe('fallback');
		expect(guard('image:a', 'summary', boom, 'fallback')).toBe('fallback');
		expect(logger.error).toHaveBeenCalledTimes(1);
		expect(hasFailed('image:a')).toBe(true);
		guard('image:b', 'summary', boom, null);
		expect(logger.error).toHaveBeenCalledTimes(2);
	});

	it('reports again after the failure is cleared', () => {
		const boom = () => {
			throw new Error('boom');
		};
		guard('image:a', 'summary', boom, null);
		clearFailure('image:a');
		expect(hasFailed('image:a')).toBe(false);
		guard('image:a', 'summary', boom, null);
		expect(logger.error).toHaveBeenCalledTimes(2);
	});

	it('tells listeners about a new failure after the current call has returned', async () => {
		const listener = vi.fn();
		const off = onGuardFailure(listener);
		guard('image:a', 'summary', () => {
			throw new Error('boom');
		}, null);
		expect(listener).not.toHaveBeenCalled();
		await Promise.resolve();
		expect(listener).toHaveBeenCalledTimes(1);
		off();
	});
});

describe('guardAsync', () => {
	it('swallows a rejection and returns the fallback', async () => {
		await expect(guardAsync('image:a', 'preview', async () => Promise.reject(new Error('no')), 'fallback')).resolves.toBe('fallback');
		expect(hasFailed('image:a')).toBe(true);
	});

	it('gives up on a call that never finishes', async () => {
		vi.useFakeTimers();
		const pending = guardAsync('image:a', 'preview', () => new Promise<string>(() => undefined), 'fallback', 50);
		await vi.advanceTimersByTimeAsync(60);
		await expect(pending).resolves.toBe('fallback');
		vi.useRealTimers();
	});

	it('lets an abort through untouched and does not mark the entry failed', async () => {
		const abort = () => Promise.reject(new DOMException('Aborted', 'AbortError'));
		await expect(guardAsync('image:a', 'preview', abort, 'fallback')).rejects.toMatchObject({ name: 'AbortError' });
		expect(hasFailed('image:a')).toBe(false);
	});
});
