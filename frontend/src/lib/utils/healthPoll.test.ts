import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { waitForHealthy } from './healthPoll';

describe('waitForHealthy', () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('resolves true as soon as check() reports healthy', async () => {
		const check = vi.fn().mockResolvedValue(true);

		const promise = waitForHealthy({ check, initialDelayMs: 1500, intervalMs: 1000, timeoutMs: 120000 });
		await vi.advanceTimersByTimeAsync(1500); // initial delay before first check

		await expect(promise).resolves.toBe(true);
		expect(check).toHaveBeenCalledTimes(1);
	});

	it('retries through a drop (rejection), a refusal (resolves false), then succeeds', async () => {
		const check = vi
			.fn()
			.mockRejectedValueOnce(new Error('ECONNRESET')) // connection dropped mid-restart
			.mockResolvedValueOnce(false) // connection refused / non-200
			.mockResolvedValueOnce(true); // backend is back

		const promise = waitForHealthy({ check, initialDelayMs: 1500, intervalMs: 1000, timeoutMs: 120000 });

		await vi.advanceTimersByTimeAsync(1500); // initial delay -> attempt 1 (drop)
		await vi.advanceTimersByTimeAsync(1000); // interval -> attempt 2 (refused)
		await vi.advanceTimersByTimeAsync(1000); // interval -> attempt 3 (200)

		await expect(promise).resolves.toBe(true);
		expect(check).toHaveBeenCalledTimes(3);
	});

	it('treats a check() that never resolves as "not yet" and keeps polling', async () => {
		// A check that never settles (stuck connection) must not stall the whole
		// poll - it should be timed out per-attempt.
		let resolveHang: ((v: boolean) => void) | undefined;
		const check = vi
			.fn()
			.mockImplementationOnce(() => new Promise<boolean>((resolve) => (resolveHang = resolve))) // hangs forever
			.mockResolvedValueOnce(true);

		const promise = waitForHealthy({ check, initialDelayMs: 1500, intervalMs: 1000, timeoutMs: 120000 });

		await vi.advanceTimersByTimeAsync(1500); // initial delay -> attempt 1 starts (hangs)
		await vi.advanceTimersByTimeAsync(1000); // attempt 1 times out (raced against intervalMs)
		await vi.advanceTimersByTimeAsync(1000); // attempt 2 -> healthy

		await expect(promise).resolves.toBe(true);
		expect(check).toHaveBeenCalledTimes(2);
		// Never let the hung call actually resolve - proves the poll didn't wait on it.
		expect(resolveHang).toBeDefined();
	});

	it('gives up after the overall timeout budget, not after a fixed attempt count', async () => {
		const check = vi.fn().mockResolvedValue(false);

		const promise = waitForHealthy({ check, initialDelayMs: 1000, intervalMs: 1000, timeoutMs: 3000 });

		await vi.advanceTimersByTimeAsync(1000 + 3000 + 1000); // initial delay + budget + one more interval for safety

		await expect(promise).resolves.toBe(false);
	});
});
