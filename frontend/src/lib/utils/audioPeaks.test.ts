import { describe, expect, it } from 'vitest';
import { computePeaks, resamplePeaks, createFlatWaveform } from './audioPeaks';

describe('computePeaks', () => {
	it('reduces a single-channel buffer to one peak per bar', () => {
		// 8 samples, 4 bars -> 2 samples per bar.
		const channel = [0.1, 0.2, 0.9, -0.3, 0.05, 0.05, -0.8, 0.1];
		const peaks = computePeaks([channel], 4);
		expect(peaks).toEqual([0.2, 0.9, 0.05, 0.8]);
	});

	it('takes the loudest channel at each sample when downmixing stereo', () => {
		const left = [0.1, 0.1, 0.1, 0.1];
		const right = [0.9, -0.9, 0.2, 0.2];
		const peaks = computePeaks([left, right], 2);
		expect(peaks).toEqual([0.9, 0.2]);
	});

	it('handles channels of unequal length by treating missing samples as silent', () => {
		const short = [0.5];
		const long = [0.1, 0.1, 0.9, 0.1];
		const peaks = computePeaks([short, long], 2);
		// Bar 0 spans indices [0,2): short contributes only index 0 (0.5),
		// long contributes indices 0-1 (0.1, 0.1) -> max 0.5.
		// Bar 1 spans indices [2,4): short has nothing there, long has 0.9, 0.1 -> max 0.9.
		expect(peaks).toEqual([0.5, 0.9]);
	});

	it('clamps sample magnitudes into [0, 1]', () => {
		const channel = [1.5, -2.0, 0.3];
		const peaks = computePeaks([channel], 1);
		expect(peaks).toEqual([1]);
	});

	it('returns a flat waveform when every channel is empty', () => {
		expect(computePeaks([[], []], 3)).toEqual(createFlatWaveform(3));
	});

	it('returns an empty array for a non-positive bar count', () => {
		expect(computePeaks([[0.1, 0.2]], 0)).toEqual([]);
		expect(computePeaks([[0.1, 0.2]], -5)).toEqual([]);
	});

	it('handles a buffer shorter than the requested bar count', () => {
		// 2 samples, 5 bars: each bar must still get a value, no NaNs/undefined.
		const peaks = computePeaks([[0.4, 0.8]], 5);
		expect(peaks).toHaveLength(5);
		expect(peaks.every((p) => Number.isFinite(p) && p >= 0 && p <= 1)).toBe(true);
		// The loudest sample (0.8) must show up somewhere in the reduction.
		expect(Math.max(...peaks)).toBeCloseTo(0.8);
	});

	it('handles no channels at all', () => {
		expect(computePeaks([], 4)).toEqual(createFlatWaveform(4));
	});
});

describe('resamplePeaks', () => {
	it('returns a copy, not the same reference, when the bar count matches', () => {
		const peaks = [0.1, 0.2, 0.3];
		const result = resamplePeaks(peaks, 3);
		expect(result).toEqual(peaks);
		expect(result).not.toBe(peaks);
	});

	it('downsamples via block-max reduction', () => {
		const peaks = [0.1, 0.9, 0.2, 0.8, 0.1, 0.1];
		expect(resamplePeaks(peaks, 3)).toEqual([0.9, 0.8, 0.1]);
	});

	it('upsamples by repeating nearby values without inventing new maxima', () => {
		const peaks = [0.2, 0.6];
		const result = resamplePeaks(peaks, 4);
		expect(result).toHaveLength(4);
		expect(Math.max(...result)).toBeLessThanOrEqual(0.6);
		expect(Math.min(...result)).toBeGreaterThanOrEqual(0);
	});

	it('returns a flat waveform for an empty source array', () => {
		expect(resamplePeaks([], 4)).toEqual(createFlatWaveform(4));
	});

	it('returns an empty array for a non-positive bar count', () => {
		expect(resamplePeaks([0.1, 0.2], 0)).toEqual([]);
	});
});

describe('createFlatWaveform', () => {
	it('fills the requested length with the given amplitude', () => {
		expect(createFlatWaveform(3, 0.4)).toEqual([0.4, 0.4, 0.4]);
	});

	it('defaults to a low, non-zero amplitude so the fallback is visible but muted', () => {
		const flat = createFlatWaveform(2);
		expect(flat).toHaveLength(2);
		expect(flat[0]).toBeGreaterThan(0);
		expect(flat[0]).toBeLessThan(0.5);
	});

	it('returns an empty array for a non-positive bar count', () => {
		expect(createFlatWaveform(0)).toEqual([]);
	});
});
