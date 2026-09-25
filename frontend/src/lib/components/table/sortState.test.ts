import { describe, it, expect } from 'vitest';
import { cycleSort } from './sortState';

describe('cycleSort', () => {
	it('sorts a fresh column ascending', () => {
		expect(cycleSort(null, 'name')).toEqual({ key: 'name', dir: 'asc' });
	});

	it('flips asc to desc on the same column', () => {
		expect(cycleSort({ key: 'name', dir: 'asc' }, 'name')).toEqual({ key: 'name', dir: 'desc' });
	});

	it('clears the sort after desc on the same column', () => {
		expect(cycleSort({ key: 'name', dir: 'desc' }, 'name')).toBeNull();
	});

	it('resets to ascending when a different column is clicked', () => {
		expect(cycleSort({ key: 'name', dir: 'desc' }, 'email')).toEqual({ key: 'email', dir: 'asc' });
	});
});
