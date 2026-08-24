// @vitest-environment jsdom
//
// CollectionTreePicker is the single nested-tree renderer shared by the "Move
// to…" target picker and every "Add to collection" surface (history/library/
// models toolbars, prompts, model details). Pins the behaviors a caller
// depends on: depth-based indentation, blocked-id filtering (move only),
// the optional root/"un-parent" row, and the empty state.
import { describe, it, expect, vi, afterEach } from 'vitest';

const { default: CollectionTreePicker } = await import(
	'../../src/lib/components/collections/CollectionTreePicker.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

interface Fixture {
	id: string;
	parent_id: string | null;
	name: string;
	item_count: number;
}

function collection(id: string, parent_id: string | null, name = id): Fixture {
	return { id, parent_id, name, item_count: 0 };
}

function mountPicker(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: CollectionTreePicker as never,
		target,
		props
	});
	return {
		target,
		rows: () =>
			Array.from(target.querySelectorAll<HTMLButtonElement>('button[role="menuitem"]')),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountPicker> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('CollectionTreePicker', () => {
	it('indents nested rows by depth and orders parents before children', () => {
		const collections = [
			collection('a', null, 'Alpha'),
			collection('a-child', 'a', 'Alpha Child'),
			collection('b', null, 'Beta')
		];
		mounted = mountPicker({ collections, onSelect: () => {} });

		const rows = mounted.rows();
		expect(rows.map((r) => r.textContent?.trim())).toEqual(['Alpha', 'Alpha Child', 'Beta']);
		expect(rows[0].getAttribute('style')).toContain('padding-left: 8px');
		expect(rows[1].getAttribute('style')).toContain('padding-left: 20px');
	});

	it('excludes blocked ids from the rendered rows', () => {
		const collections = [
			collection('a', null, 'Alpha'),
			collection('a-child', 'a', 'Alpha Child'),
			collection('b', null, 'Beta')
		];
		mounted = mountPicker({
			collections,
			blockedIds: new Set(['a', 'a-child']),
			onSelect: () => {}
		});

		const rows = mounted.rows();
		expect(rows.map((r) => r.textContent?.trim())).toEqual(['Beta']);
	});

	it('calls onSelect with the collection id when a row is clicked', () => {
		const onSelect = vi.fn();
		const collections = [collection('a', null, 'Alpha')];
		mounted = mountPicker({ collections, onSelect });

		mounted.rows()[0].click();
		expect(onSelect).toHaveBeenCalledWith('a');
	});

	it('renders a root row that resolves to null only when showRoot is set', () => {
		const onSelect = vi.fn();
		const collections = [collection('a', null, 'Alpha')];
		mounted = mountPicker({
			collections,
			onSelect,
			showRoot: true,
			rootLabel: 'Top level'
		});

		const rows = mounted.rows();
		expect(rows[0].textContent).toContain('Top level');
		rows[0].click();
		expect(onSelect).toHaveBeenCalledWith(null);
	});

	it('shows the empty message when there are no selectable targets', () => {
		mounted = mountPicker({ collections: [], onSelect: () => {}, emptyMessage: 'Nothing here' });
		expect(mounted.target.textContent).toContain('Nothing here');
	});
});
