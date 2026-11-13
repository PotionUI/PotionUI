import { describe, expect, it } from 'vitest';
import {
	formatGenerationTime,
	generationQueueLabel,
	generationRunSummary,
	formatHumanDuration,
	formatRunLine
} from './generationTime';

const file = (over: Record<string, unknown> = {}) =>
	({ id: 1, file_path: 'a.png', file_type: 'IMAGE', is_final: true, created_at: '', ...over }) as never;

const done = (over: Record<string, unknown> = {}) =>
	({
		status: 'completed',
		completed_at: '2026-01-01T00:00:00Z',
		run_seconds: 92,
		queued_seconds: 0,
		files: [file()],
		...over
	}) as never;

describe('formatGenerationTime', () => {
	it.each([
		[0, '<1 s'],
		[8, '8 s'],
		[10, '10 s'],
		[48, '48 s'],
		[59.6, '1:00'],
		[92, '1:32'],
		[3599, '59:59'],
		[3725, '1:02:05']
	])('%s -> %s', (input, expected) => {
		expect(formatGenerationTime(input)).toBe(expected);
	});

	it.each([[null], [undefined], [-3], [NaN], [Infinity]])('%s -> nothing', (input) => {
		expect(formatGenerationTime(input as never)).toBeNull();
	});
});

describe('generationRunSummary', () => {
	it('shows the time alone for a single image', () => {
		expect(generationRunSummary(done())).toBe('1:32');
	});

	it('adds the image count for several images, ignoring derived and non-image files', () => {
		const files = [file(), file(), file({ is_derived: true }), file({ file_type: 'VIDEO' })];
		expect(generationRunSummary(done({ files }))).toBe('1:32 · 2 images');
	});

	it('shows <1 s for a run that rounds to zero', () => {
		expect(generationRunSummary(done({ run_seconds: 0 }))).toBe('<1 s');
	});

	it('shows nothing without timing, completion or when not completed', () => {
		expect(generationRunSummary(done({ run_seconds: null }))).toBeNull();
		expect(generationRunSummary(done({ run_seconds: undefined }))).toBeNull();
		expect(generationRunSummary(done({ completed_at: undefined }))).toBeNull();
		expect(generationRunSummary(done({ status: 'running' }))).toBeNull();
		expect(generationRunSummary(done({ status: 'failed' }))).toBeNull();
	});
});

describe('generationQueueLabel', () => {
	it('appears from 5 s of waiting', () => {
		expect(generationQueueLabel(done({ queued_seconds: 4 }))).toBeNull();
		expect(generationQueueLabel(done({ queued_seconds: 5 }))).toBe('5 s');
		expect(generationQueueLabel(done({ queued_seconds: 75 }))).toBe('1:15');
	});

	it('is absent when the stamp is missing', () => {
		expect(generationQueueLabel(done({ queued_seconds: null }))).toBeNull();
		expect(generationQueueLabel(done({ queued_seconds: undefined }))).toBeNull();
	});
});

describe('formatHumanDuration', () => {
	it('writes seconds, minutes and hours in words', () => {
		expect(formatHumanDuration(0.2)).toBe('under 1 s');
		expect(formatHumanDuration(38)).toBe('38 s');
		expect(formatHumanDuration(102)).toBe('1 min 42 s');
		expect(formatHumanDuration(382)).toBe('6 min 22 s');
		expect(formatHumanDuration(360)).toBe('6 min');
		expect(formatHumanDuration(3780)).toBe('1 h 3 min');
		expect(formatHumanDuration(7200)).toBe('2 h');
	});

	it('returns null for unusable input', () => {
		expect(formatHumanDuration(null)).toBeNull();
		expect(formatHumanDuration(undefined)).toBeNull();
		expect(formatHumanDuration(-1)).toBeNull();
		expect(formatHumanDuration(Number.NaN)).toBeNull();
	});
});

describe('formatRunLine', () => {
	const base = {
		status: 'completed' as const,
		run_seconds: 102,
		queued_seconds: null,
		created_at: '2026-11-12T20:40:00Z',
		completed_at: '2026-11-12T20:49:00Z',
		files: []
	};
	const options = { locale: 'en-GB', timeZone: 'UTC', now: new Date('2026-11-13T00:00:00Z') };

	it('writes when and how long for a completed run', () => {
		expect(formatRunLine(base, options)).toEqual({ when: '12 Nov, 20:49', detail: 'took 1 min 42 s' });
	});

	it('adds the image count above one and the queue wait from five seconds', () => {
		const files = Array.from({ length: 4 }, () => ({ is_final: true, is_derived: false, file_type: 'image' }));
		const line = formatRunLine({ ...base, files: files as never, queued_seconds: 12 }, options);
		expect(line.detail).toBe('took 1 min 42 s · 4 images · queued 12 s');
		expect(formatRunLine({ ...base, queued_seconds: 4 }, options).detail).toBe('took 1 min 42 s');
	});

	it('handles a run under one second and one over an hour', () => {
		expect(formatRunLine({ ...base, run_seconds: 0.3 }, options).detail).toBe('took under 1 s');
		expect(formatRunLine({ ...base, run_seconds: 3900 }, options).detail).toBe('took 1 h 5 min');
	});

	it('says failed or cancelled after the time it ran', () => {
		const failed = { ...base, status: 'failed' as const, run_seconds: 38, completed_at: undefined };
		expect(formatRunLine(failed, options)).toEqual({ when: '12 Nov, 20:40', detail: 'failed after 38 s' });
		expect(formatRunLine({ ...failed, status: 'cancelled' as const }, options).detail).toBe('cancelled after 38 s');
	});

	it('gives only the date when nothing was timed', () => {
		expect(formatRunLine({ ...base, run_seconds: null }, options).detail).toBeNull();
		expect(formatRunLine({ ...base, status: 'failed', run_seconds: null }, options).detail).toBeNull();
		expect(formatRunLine({ ...base, completed_at: undefined }, options)).toEqual({
			when: '12 Nov, 20:40',
			detail: null
		});
	});

	it('adds the year for another year', () => {
		const line = formatRunLine(base, { ...options, now: new Date('2027-02-01T00:00:00Z') });
		expect(line.when).toContain('2026');
	});
});
