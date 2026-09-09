import { describe, it, expect } from 'vitest';
import { runDuration, runStartedLabel, mergeRunHistory } from './recipeRunHistory';

describe('runDuration', () => {
	it('measures a finished run between its two timestamps', () => {
		expect(
			runDuration({
				created_at: '2026-09-09T10:00:00Z',
				completed_at: '2026-09-09T10:02:30Z'
			})
		).toBe('2m 30s');
	});

	it('runs the clock against now for a run still going', () => {
		const now = () => Date.parse('2026-09-09T10:00:45Z');
		expect(runDuration({ created_at: '2026-09-09T10:00:00Z', completed_at: null }, now)).toBe('45s');
	});

	it('returns null when the run never started', () => {
		expect(runDuration({ created_at: null, completed_at: null })).toBeNull();
	});

	it('returns null for unparseable timestamps rather than NaN', () => {
		expect(runDuration({ created_at: 'not a date', completed_at: null })).toBeNull();
	});

	it('returns null when the run finished before it started', () => {
		expect(
			runDuration({
				created_at: '2026-09-09T10:05:00Z',
				completed_at: '2026-09-09T10:00:00Z'
			})
		).toBeNull();
	});
});

describe('runStartedLabel', () => {
	it('renders a run with no timestamp as an em dash', () => {
		expect(runStartedLabel({ created_at: null })).toBe('—');
		expect(runStartedLabel({ created_at: 'nonsense' })).toBe('—');
	});

	it('renders a real timestamp as a date and time', () => {
		const label = runStartedLabel({ created_at: '2026-09-09T10:00:00Z' });
		expect(label).not.toBe('—');
		expect(label).toMatch(/\d/);
	});
});

describe('mergeRunHistory', () => {
	const history = [{ id: 'b', n: 2 }, { id: 'a', n: 1 }];

	it('returns the history untouched when there is no live run', () => {
		expect(mergeRunHistory(history, null)).toBe(history);
	});

	it('replaces the history copy in place when the live run is already listed', () => {
		const merged = mergeRunHistory(history, { id: 'b', n: 99 });
		expect(merged).toEqual([{ id: 'b', n: 99 }, { id: 'a', n: 1 }]);
		expect(history[0].n).toBe(2);
	});

	it('prepends a live run the history has not caught up with', () => {
		expect(mergeRunHistory(history, { id: 'c', n: 3 })).toEqual([
			{ id: 'c', n: 3 },
			{ id: 'b', n: 2 },
			{ id: 'a', n: 1 }
		]);
	});
});
