import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync, createRawSnippet } from 'svelte';
import { reactiveProps } from './stubs/reactiveProps.svelte';

const { default: DataTable } = await import('../../src/lib/components/table/DataTable.svelte');

type Row = { id: string; name: string };

function makeRows(count: number): Row[] {
	return Array.from({ length: count }, (_, i) => ({ id: `r${i}`, name: `Row ${i}` }));
}

const nameCell = createRawSnippet((row: () => Row) => ({
	render: () => `<span><a href="/detail" data-link>${row().name}</a></span>`
}));

const columns = [{ key: 'name', label: 'Name', width: '1fr', cell: nameCell }];

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function setup(overrides: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	const live = reactiveProps<Record<string, unknown>>({
		columns,
		rows: makeRows(6),
		getRowId: (row: Row) => row.id,
		rowSelect: true,
		selected: new Set<string>(),
		onSelectedChange: (next: Set<string>) => live.set({ selected: next }),
		...overrides
	});
	component = mount(DataTable as any, { target, props: live.props as any });
	flushSync();
	return live;
}

function rows(): HTMLElement[] {
	return Array.from(target.querySelectorAll<HTMLElement>('[role="row"][data-row-id]'));
}

function selectedIds(): string[] {
	return rows()
		.filter((row) => row.getAttribute('aria-selected') === 'true')
		.map((row) => row.dataset.rowId!);
}

function key(el: HTMLElement, init: KeyboardEventInit) {
	el.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init }));
	flushSync();
}

afterEach(() => {
	if (component) unmount(component);
	component = null;
	target?.remove();
});

describe('SelectableGrid row selection', () => {
	it('exposes grid, row and aria-selected semantics without nested buttons', () => {
		setup();
		expect(target.querySelector('[role="grid"]')).not.toBeNull();
		expect(rows()).toHaveLength(6);
		expect(rows()[0].getAttribute('aria-selected')).toBe('false');
		expect(rows()[0].querySelector('button')).toBeNull();
	});

	it('toggles a row when any part of it is clicked', () => {
		setup();
		rows()[2].click();
		flushSync();
		expect(selectedIds()).toEqual(['r2']);
		rows()[2].click();
		flushSync();
		expect(selectedIds()).toEqual([]);
	});

	it('does not toggle when an inner link is clicked', () => {
		setup();
		const link = target.querySelectorAll<HTMLElement>('[data-link]')[1];
		link.addEventListener('click', (event) => event.preventDefault());
		link.click();
		flushSync();
		expect(selectedIds()).toEqual([]);
	});

	it('selects a range on shift-click from the anchor', () => {
		setup();
		rows()[1].click();
		flushSync();
		rows()[4].dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true }));
		flushSync();
		expect(selectedIds()).toEqual(['r1', 'r2', 'r3', 'r4']);
	});

	it('shift-click after deselecting the anchor clears the range', () => {
		setup({ selected: new Set(['r0', 'r1', 'r2', 'r3']) });
		rows()[0].click();
		flushSync();
		rows()[2].dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true }));
		flushSync();
		expect(selectedIds()).toEqual(['r3']);
	});

	it('skips locked rows and marks them aria-disabled', () => {
		setup({ isLocked: (row: Row) => row.id === 'r1' });
		expect(rows()[1].getAttribute('aria-disabled')).toBe('true');
		rows()[1].click();
		flushSync();
		rows()[0].click();
		rows()[2].dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true }));
		flushSync();
		expect(selectedIds()).toEqual(['r0', 'r2']);
	});

	it('single mode replaces the selection', () => {
		setup({ selectionType: 'single' });
		rows()[0].click();
		flushSync();
		rows()[3].click();
		flushSync();
		expect(selectedIds()).toEqual(['r3']);
	});
});

describe('SelectableGrid keyboard', () => {
	it('moves the active row with arrow keys and toggles with space', () => {
		setup();
		rows()[0].focus();
		key(rows()[0], { key: 'ArrowDown' });
		key(rows()[1], { key: ' ' });
		expect(selectedIds()).toEqual(['r1']);
		key(rows()[1], { key: 'ArrowUp' });
		key(rows()[0], { key: ' ' });
		expect(selectedIds()).toEqual(['r0', 'r1']);
	});

	it('extends the selection with shift and arrows', () => {
		setup();
		rows()[1].focus();
		rows()[1].click();
		flushSync();
		key(rows()[1], { key: 'ArrowDown', shiftKey: true });
		key(rows()[2], { key: 'ArrowDown', shiftKey: true });
		expect(selectedIds()).toEqual(['r1', 'r2', 'r3']);
	});

	it('calls onSelectAll on ctrl+a and onConfirm on ctrl+enter', () => {
		let all = 0;
		let confirm = 0;
		setup({ onSelectAll: () => all++, onConfirm: () => confirm++ });
		rows()[0].focus();
		key(rows()[0], { key: 'a', ctrlKey: true });
		key(rows()[0], { key: 'Enter', ctrlKey: true });
		expect(all).toBe(1);
		expect(confirm).toBe(1);
	});

	it('keeps exactly one row tabbable', () => {
		setup();
		expect(rows().filter((row) => row.tabIndex === 0)).toHaveLength(1);
	});
});

describe('SelectableGrid virtualisation', () => {
	it('renders every row at or below 150', () => {
		setup({ rows: makeRows(150), virtual: true });
		expect(rows()).toHaveLength(150);
	});

	it('windows rows above 150', () => {
		setup({ rows: makeRows(400), virtual: true });
		expect(rows().length).toBeGreaterThan(0);
		expect(rows().length).toBeLessThan(60);
		expect(target.querySelector('[role="grid"]')!.getAttribute('aria-rowcount')).toBe('401');
	});
});
