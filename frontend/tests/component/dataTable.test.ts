import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync, createRawSnippet } from 'svelte';

const { default: DataTable } = await import('../../src/lib/components/table/DataTable.svelte');

import type { DataTableColumn } from '../../src/lib/components/table/column';

type Row = { id: string; name: string; email: string };

const ROWS: Row[] = [
	{ id: 'a', name: 'admin', email: 'admin@potionui.local' },
	{ id: 'b', name: 'someuser', email: 'someuser@potionui.local' }
];

const columns: DataTableColumn<any>[] = [
	{ key: 'name', label: 'Username', width: '1fr', sortable: true, accessor: (row: Row) => row.name },
	{ key: 'email', label: 'Email', width: '200px', accessor: (row: Row) => row.email },
	{ key: 'internal', label: 'Internal', width: '80px', priority: 2 as const, accessor: () => 'secret' }
];

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function mountTable(overrides: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(DataTable, {
		target,
		props: {
			columns,
			rows: ROWS,
			getRowId: (row: any) => row.id,
			...overrides
		}
	});
	flushSync();
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

describe('DataTable', () => {
	it('renders one row per item using each column accessor', () => {
		mountTable();
		expect(target.textContent).toContain('admin');
		expect(target.textContent).toContain('admin@potionui.local');
		expect(target.textContent).toContain('someuser');
	});

	it('hides priority-2 columns unless detail is set', () => {
		mountTable();
		expect(target.textContent).not.toContain('secret');
		unmount(component!);
		component = null;
		target.remove();

		mountTable({ detail: true });
		expect(target.textContent).toContain('secret');
	});

	it('cycles sort asc -> desc -> off on repeated header clicks', () => {
		const onSortChange = vi.fn();
		mountTable({ onSortChange });
		const header = Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Username'))!;
		header.click();
		expect(onSortChange).toHaveBeenLastCalledWith({ key: 'name', dir: 'asc' });

		unmount(component!);
		component = null;
		target.remove();
		mountTable({ onSortChange, sort: { key: 'name', dir: 'asc' } });
		const header2 = Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Username'))!;
		header2.click();
		expect(onSortChange).toHaveBeenLastCalledWith({ key: 'name', dir: 'desc' });
	});

	it('fires onRowClick when a row is clicked, but not when the checkbox is clicked', () => {
		const onRowClick = vi.fn();
		const onSelectedChange = vi.fn();
		mountTable({ onRowClick, selected: new Set<string>(), onSelectedChange });
		const rows = target.querySelectorAll('[role="row"]');
		(rows[0] as HTMLElement).click();
		expect(onRowClick).toHaveBeenCalledWith(ROWS[0]);

		onRowClick.mockClear();
		const checkbox = rows[1].querySelector('button[role="checkbox"]') as HTMLButtonElement;
		checkbox.click();
		expect(onRowClick).not.toHaveBeenCalled();
		expect(onSelectedChange).toHaveBeenCalledWith(new Set(['b']));
	});

	it('selects and clears the whole page from the header checkbox', () => {
		const onSelectedChange = vi.fn();
		mountTable({ selected: new Set<string>(), onSelectedChange });
		const headCheckbox = target.querySelector('[role="checkbox"][aria-label="Select all rows on this page"]') as HTMLButtonElement;
		headCheckbox.click();
		expect(onSelectedChange).toHaveBeenCalledWith(new Set(['a', 'b']));

		unmount(component!);
		component = null;
		target.remove();
		mountTable({ selected: new Set(['a', 'b']), onSelectedChange });
		const headCheckbox2 = target.querySelector('[role="checkbox"][aria-label="Select all rows on this page"]') as HTMLButtonElement;
		expect(headCheckbox2.getAttribute('aria-checked')).toBe('true');
		headCheckbox2.click();
		expect(onSelectedChange).toHaveBeenLastCalledWith(new Set());
	});

	it('renders the loading skeleton instead of rows while loading', () => {
		mountTable({ loading: true, loadingRowCount: 2 });
		expect(target.textContent).not.toContain('admin');
		expect(target.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);
	});

	it('renders the empty state when there are no rows, and the filtered-empty state when isFiltered', () => {
		const emptyState = createRawSnippet(() => ({ render: () => `<p>Nothing here yet</p>` }));
		const filteredEmptyState = createRawSnippet(() => ({ render: () => `<p>No matches</p>` }));
		mountTable({ rows: [], emptyState, filteredEmptyState, isFiltered: false });
		expect(target.textContent).toContain('Nothing here yet');

		unmount(component!);
		component = null;
		target.remove();
		mountTable({ rows: [], emptyState, filteredEmptyState, isFiltered: true });
		expect(target.textContent).toContain('No matches');
	});
});
