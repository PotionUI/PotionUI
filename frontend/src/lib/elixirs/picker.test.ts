import { describe, expect, it } from 'vitest';
import { byRecent, copyName, deleteBlocked, manageRows, newElixirLabel, newElixirName, pickerRows, rowStatus, stepWord, type PickerSummary } from './picker';

const make = (id: string, name: string, updated: string, extra: Partial<PickerSummary> = {}): PickerSummary => ({ id, name, updated_at: updated, node_count: 2, ...extra });

const list = [
	make('a', 'Potion study', '2026-11-01T10:00:00Z', { status: 'done' }),
	make('b', 'Lookbook loop', '2026-11-03T10:00:00Z', { status: 'brewing' }),
	make('c', 'Moon set', '2026-11-02T10:00:00Z', { status: 'awaiting' }),
	make('d', 'Glass', '2026-10-01T10:00:00Z', { status: 'attention' }),
	make('e', 'Orbit', '2026-10-02T10:00:00Z'),
	make('f', 'Portrait potion', '2026-09-01T10:00:00Z')
];

describe('elixir picker rows', () => {
	it('lists the five most recently changed elixirs', () => {
		expect(pickerRows(list, '').map((item) => item.id)).toEqual(['b', 'c', 'a', 'e', 'd']);
		expect(byRecent([make('x', 'x', 'nope'), make('y', 'y', '2026-01-01T00:00:00Z')]).map((item) => item.id)).toEqual(['y', 'x']);
	});

	it('matches a search by name, case-insensitively', () => {
		expect(pickerRows(list, '  POTION ').map((item) => item.id)).toEqual(['a', 'f']);
		expect(pickerRows(list, 'nothing')).toEqual([]);
	});

	it('names the new elixir after the search', () => {
		expect(newElixirLabel('')).toBe('New elixir');
		expect(newElixirLabel(' Dusk ')).toBe('New elixir “Dusk”');
		expect(newElixirName('  ')).toBe('Untitled elixir');
		expect(newElixirName(' Dusk ')).toBe('Dusk');
		expect(copyName('Dusk')).toBe('Dusk copy');
		expect(stepWord(1)).toBe('step');
		expect(stepWord(3)).toBe('steps');
	});
});

describe('elixir manager', () => {
	it('filters by brew state and search', () => {
		expect(manageRows(list, '', 'all')).toHaveLength(6);
		expect(manageRows(list, '', 'brewing').map((item) => item.id)).toEqual(['b']);
		expect(manageRows(list, '', 'awaiting').map((item) => item.id)).toEqual(['c']);
		expect(manageRows(list, '', 'attention').map((item) => item.id)).toEqual(['d']);
		expect(manageRows(list, 'moon', 'all').map((item) => item.id)).toEqual(['c']);
	});

	it('blocks delete while it brews or waits for a choice', () => {
		expect(deleteBlocked(list[1])).toBe('Stop its brew first');
		expect(deleteBlocked(list[2])).toBe('Stop its brew first');
		expect(deleteBlocked(list[0])).toBeNull();
		expect(deleteBlocked(list[0], { tone: 'signal', label: 'Brewing' })).toBe('Stop its brew first');
	});
});

describe('row status', () => {
	it('reads the summary status, or the open tab status first', () => {
		expect(rowStatus(list[1])).toMatchObject({ label: 'Brewing', tone: 'signal', pulse: true, live: true, ring: false });
		expect(rowStatus(list[2])).toMatchObject({ label: 'Waiting for your choice', ring: true, live: true });
		expect(rowStatus(list[0])).toMatchObject({ label: 'Done', tone: 'success', live: false });
		expect(rowStatus(make('z', 'z', '', { node_count: 0 }))).toMatchObject({ label: 'No steps yet', tone: 'neutral' });
		expect(rowStatus(list[0], { tone: 'danger', label: 'Brew failed' })).toMatchObject({ label: 'Brew failed', tone: 'danger', live: false });
	});
});
