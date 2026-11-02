import { describe, expect, it, vi } from 'vitest';
import { fillOrder, loadPeaks, runStrip, stripTimes, type PeakDeps } from './stripScheduler';

describe('strip planning', () => {
	it('samples the middle of each cell and stays inside the clip', () => {
		expect(stripTimes(8, 4)).toEqual([1, 3, 5, 7]);
		expect(stripTimes(0, 4)).toEqual([]);
		expect(stripTimes(0.04, 1)[0]).toBe(0);
	});

	it('fills coarse to fine and visits every cell once', () => {
		const order = fillOrder(9);
		expect(order.slice(0, 3)).toEqual([0, 8, 4]);
		expect([...order].sort((a, b) => a - b)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8]);
		expect(fillOrder(1)).toEqual([0]);
		expect(fillOrder(0)).toEqual([]);
	});
});

describe('runStrip', () => {
	it('hands every result back by its cell index', async () => {
		const results: Array<[number, string]> = [];
		const outcome = await runStrip({
			count: 4,
			signal: new AbortController().signal,
			work: async (index) => `frame-${index}`,
			onResult: (index, value) => results.push([index, value])
		});
		expect(outcome).toBe('done');
		expect(results.sort((a, b) => a[0] - b[0])).toEqual([
			[0, 'frame-0'],
			[1, 'frame-1'],
			[2, 'frame-2'],
			[3, 'frame-3']
		]);
	});

	it('stops at once when cancelled and drops the frame in flight', async () => {
		const controller = new AbortController();
		const results: number[] = [];
		const work = vi.fn(async (index: number) => {
			if (index === 3) controller.abort();
			return index;
		});
		const outcome = await runStrip({ count: 6, signal: controller.signal, work, onResult: (index) => results.push(index) });
		expect(outcome).toBe('cancelled');
		expect(results).toEqual([0, 5, 2, 1]);
		expect(work).toHaveBeenCalledTimes(5);
	});

	it('keeps going past a frame that fails', async () => {
		const errors: number[] = [];
		const results: number[] = [];
		await runStrip({
			count: 3,
			signal: new AbortController().signal,
			work: async (index) => {
				if (index === 2) throw new Error('decode');
				return index;
			},
			onResult: (index) => results.push(index),
			onError: (index) => errors.push(index)
		});
		expect(errors).toEqual([2]);
		expect(results.sort()).toEqual([0, 1]);
	});
});

describe('loadPeaks', () => {
	function deps(overrides: Partial<PeakDeps> = {}): PeakDeps {
		return {
			fetch: async () => ({ ok: true, headers: { get: () => '100' }, arrayBuffer: async () => new ArrayBuffer(100) }),
			decode: async () => ({ numberOfChannels: 1, getChannelData: () => Float32Array.from([0, 0.5, -1, 0.25]) }),
			...overrides
		};
	}

	it('decodes the track into bars', async () => {
		const peaks = await loadPeaks('/clip.mp4', 2, new AbortController().signal, deps());
		expect(peaks).toHaveLength(2);
		expect(Math.max(...peaks!)).toBeGreaterThan(0);
	});

	it('gives up when cancelled before the decode', async () => {
		const controller = new AbortController();
		const decode = vi.fn();
		const peaks = await loadPeaks(
			'/clip.mp4',
			4,
			controller.signal,
			deps({
				fetch: async () => {
					controller.abort();
					return { ok: true, headers: { get: () => null }, arrayBuffer: async () => new ArrayBuffer(8) };
				},
				decode
			})
		);
		expect(peaks).toBeNull();
		expect(decode).not.toHaveBeenCalled();
	});

	it('answers null for a clip without sound or one too large', async () => {
		expect(await loadPeaks('/clip.mp4', 4, new AbortController().signal, deps({ decode: async () => Promise.reject(new Error('no audio')) }))).toBeNull();
		expect(
			await loadPeaks('/clip.mp4', 4, new AbortController().signal, deps({ fetch: async () => ({ ok: true, headers: { get: () => String(1e12) }, arrayBuffer: async () => new ArrayBuffer(1) }) }))
		).toBeNull();
	});
});
