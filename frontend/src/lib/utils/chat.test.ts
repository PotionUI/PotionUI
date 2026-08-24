import { describe, it, expect } from 'vitest';
import { groupSessionsByDate } from './chat';

const NOW = new Date('2026-07-07T12:00:00');

function session(id: string, updated_at?: string, created_at?: string) {
	return { id, updated_at, created_at };
}

describe('groupSessionsByDate', () => {
	it('buckets by day distance from now', () => {
		const groups = groupSessionsByDate(
			[
				session('today', '2026-07-07T08:00:00'),
				session('yesterday', '2026-07-06T23:59:00'),
				session('this-week', '2026-07-02T10:00:00'),
				session('older', '2026-06-20T10:00:00')
			],
			NOW
		);

		expect(groups.map((g) => g.label)).toEqual(['Today', 'Yesterday', 'This week', 'Older']);
		expect(groups.map((g) => g.sessions.map((s) => s.id))).toEqual([
			['today'],
			['yesterday'],
			['this-week'],
			['older']
		]);
	});

	it('omits empty buckets and preserves input order within a bucket', () => {
		const groups = groupSessionsByDate(
			[session('a', '2026-07-07T11:00:00'), session('b', '2026-07-07T01:00:00')],
			NOW
		);

		expect(groups).toHaveLength(1);
		expect(groups[0].label).toBe('Today');
		expect(groups[0].sessions.map((s) => s.id)).toEqual(['a', 'b']);
	});

	it('boundary: 6 days ago is This week, 7 days ago is Older', () => {
		const groups = groupSessionsByDate(
			[session('six', '2026-07-01T09:00:00'), session('seven', '2026-06-30T09:00:00')],
			NOW
		);

		expect(groups.map((g) => g.label)).toEqual(['This week', 'Older']);
	});

	it('falls back to created_at, then Older for missing/invalid dates', () => {
		const groups = groupSessionsByDate(
			[session('created-only', undefined, '2026-07-07T09:00:00'), session('no-dates'), session('bad', 'nonsense')],
			NOW
		);

		expect(groups.map((g) => g.label)).toEqual(['Today', 'Older']);
		expect(groups[1].sessions.map((s) => s.id)).toEqual(['no-dates', 'bad']);
	});

	it('future timestamps land in Today', () => {
		const groups = groupSessionsByDate([session('future', '2026-07-08T09:00:00')], NOW);
		expect(groups[0].label).toBe('Today');
	});
});
