import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { requestFrame, type FrameSlot } from './frameRequest';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function harness() {
	let slot: FrameSlot | null = null;
	const revoked: string[] = [];
	let made = 0;
	const loads: AbortSignal[] = [];
	const pending: Array<(blob: Blob) => void> = [];
	const request = (key: string) =>
		requestFrame({
			key,
			load: (signal) => {
				loads.push(signal);
				return new Promise<Blob>((resolve) => pending.push(resolve));
			},
			current: () => slot,
			assign: (next) => (slot = next),
			createUrl: () => `blob:${(made += 1)}`,
			revokeUrl: (url) => revoked.push(url)
		});
	return { request, loads, pending, revoked, slot: () => slot };
}

describe('requestFrame', () => {
	it('collapses a burst of changes into one request for the last key', async () => {
		const h = harness();
		let cancel = h.request('a');
		for (const key of ['b', 'c', 'd']) {
			await vi.advanceTimersByTimeAsync(100);
			cancel();
			cancel = h.request(key);
		}
		await vi.advanceTimersByTimeAsync(250);
		expect(h.loads).toHaveLength(1);
		h.pending[0](new Blob(['x']));
		await vi.runAllTimersAsync();
		expect(h.slot()).toEqual({ key: 'd', url: 'blob:1' });
	});

	it('never assigns an answer that arrives after the request was dropped', async () => {
		const h = harness();
		const cancel = h.request('a');
		await vi.advanceTimersByTimeAsync(250);
		cancel();
		expect(h.loads[0].aborted).toBe(true);
		h.pending[0](new Blob(['late']));
		await vi.runAllTimersAsync();
		expect(h.slot()).toBeNull();
	});

	it('revokes the previous frame once when a new one lands', async () => {
		const h = harness();
		h.request('a');
		await vi.advanceTimersByTimeAsync(250);
		h.pending[0](new Blob(['1']));
		await vi.runAllTimersAsync();
		h.request('b');
		await vi.advanceTimersByTimeAsync(250);
		h.pending[1](new Blob(['2']));
		await vi.runAllTimersAsync();
		expect(h.slot()).toEqual({ key: 'b', url: 'blob:2' });
		expect(h.revoked).toEqual(['blob:1']);
	});
});
