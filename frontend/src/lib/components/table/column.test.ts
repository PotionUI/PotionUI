import { describe, it, expect } from 'vitest';
import { alignClass, gridTemplateColumns, visibleColumns, type DataTableColumn } from './column';

const columns: DataTableColumn<{ id: string }>[] = [
	{ key: 'name', label: 'Name', width: 'minmax(160px,1.4fr)', priority: 0 },
	{ key: 'email', label: 'Email', width: '200px', priority: 1 },
	{ key: 'internal', label: 'Internal', width: '120px', priority: 2 }
];

describe('visibleColumns', () => {
	it('drops priority-2 columns outside detail mode', () => {
		expect(visibleColumns(columns, { detail: false }).map((c) => c.key)).toEqual(['name', 'email']);
	});

	it('keeps priority-2 columns in detail mode', () => {
		expect(visibleColumns(columns, { detail: true }).map((c) => c.key)).toEqual(['name', 'email', 'internal']);
	});
});

describe('gridTemplateColumns', () => {
	it('includes every column width in order for the full (wide) layout', () => {
		expect(gridTemplateColumns(columns, { narrow: false })).toBe('minmax(160px,1.4fr) 200px 120px');
	});

	it('drops non-P0 columns for the narrow layout', () => {
		expect(gridTemplateColumns(columns, { narrow: true })).toBe('minmax(160px,1.4fr)');
	});

	it('pins a 36px checkbox track first and a 40px overflow track last', () => {
		expect(gridTemplateColumns(columns, { narrow: false, selectable: true, trailing: true })).toBe(
			'36px minmax(160px,1.4fr) 200px 120px 40px'
		);
	});
});

describe('alignClass', () => {
	it('defaults to left alignment', () => {
		expect(alignClass(undefined)).toContain('text-left');
	});

	it('maps right and center explicitly', () => {
		expect(alignClass('right')).toContain('text-right');
		expect(alignClass('center')).toContain('text-center');
	});
});
