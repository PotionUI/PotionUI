import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { probeServerHealth, serverIsReady, waitForHealthy } from './healthPoll';

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

function healthClient(...answers: Array<{ status: number; data?: unknown } | Error>) {
	const get = vi.fn();
	for (const answer of answers) {
		if (answer instanceof Error) get.mockRejectedValueOnce(answer);
		else get.mockResolvedValueOnce(answer);
	}
	return { get } as unknown as Parameters<typeof probeServerHealth>[0] & { get: typeof get };
}

describe('probeServerHealth', () => {
	it('reads a 200 as ready', async () => {
		await expect(probeServerHealth(healthClient({ status: 200, data: { status: 'healthy' } }))).resolves.toBe('ready');
	});

	it('reads a 503 starting answer as alive and warming up, without throwing', async () => {
		const client = healthClient({ status: 503, data: { status: 'starting', service: 'potionui-api' } });

		await expect(probeServerHealth(client)).resolves.toBe('starting');
		expect(client.get).toHaveBeenCalledWith('/health', expect.objectContaining({ timeout: 3000 }));
		const config = client.get.mock.calls[0][1];
		expect(config.validateStatus(503)).toBe(true);
	});

	it('reads any other 503 or error status as down', async () => {
		await expect(probeServerHealth(healthClient({ status: 503, data: 'Service Unavailable' }))).resolves.toBe('down');
		await expect(probeServerHealth(healthClient({ status: 502, data: { status: 'starting' } }))).resolves.toBe('down');
	});

	it('reads a dropped connection as down', async () => {
		await expect(probeServerHealth(healthClient(new Error('ECONNREFUSED')))).resolves.toBe('down');
	});

	it('counts only a ready server as ready', async () => {
		await expect(serverIsReady(healthClient({ status: 503, data: { status: 'starting' } }))).resolves.toBe(false);
		await expect(serverIsReady(healthClient({ status: 200, data: {} }))).resolves.toBe(true);
	});

	describe('while waiting for a restart', () => {
		beforeEach(() => {
			vi.useFakeTimers();
		});

		afterEach(() => {
			vi.useRealTimers();
		});

		it('keeps waiting through a warming-up server until it is ready', async () => {
			const client = healthClient(
				new Error('ECONNREFUSED'),
				{ status: 503, data: { status: 'starting' } },
				{ status: 503, data: { status: 'starting' } },
				{ status: 200, data: { status: 'healthy' } }
			);

			const promise = waitForHealthy({
				check: () => serverIsReady(client),
				initialDelayMs: 1500,
				intervalMs: 1000,
				timeoutMs: 120000
			});
			await vi.advanceTimersByTimeAsync(1500 + 3 * 1000);

			await expect(promise).resolves.toBe(true);
			expect(client.get).toHaveBeenCalledTimes(4);
		});
	});
});
