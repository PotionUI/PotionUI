import { describe, it, expect } from 'vitest';
import { buildTree, flattenTree, flattenVisible, descendantIds } from './tree';
import type { TreeItem } from './types';

interface Item extends TreeItem {
	name: string;
}

function item(id: string, parent_id: string | null, name: string): Item {
	return { id, parent_id, name };
}

describe('buildTree', () => {
	it('treats a missing/dangling parent_id as a root', () => {
		const items = [item('a', 'does-not-exist', 'A'), item('b', null, 'B')];
		const tree = buildTree(items);
		expect(tree.map((n) => n.item.id).sort()).toEqual(['a', 'b']);
		expect(tree.every((n) => n.depth === 0)).toBe(true);
	});

	it('numbers depth per level', () => {
		const items = [
			item('root', null, 'Root'),
			item('child', 'root', 'Child'),
			item('grandchild', 'child', 'Grandchild')
		];
		const tree = buildTree(items);
		expect(tree[0].depth).toBe(0);
		expect(tree[0].children[0].depth).toBe(1);
		expect(tree[0].children[0].children[0].depth).toBe(2);
	});

	it('sorts roots and each sibling group by name by default', () => {
		const items = [
			item('b', null, 'Bravo'),
			item('a', null, 'Alpha'),
			item('c1', 'a', 'Charlie'),
			item('a1', 'a', 'Alpha-child')
		];
		const tree = buildTree(items);
		expect(tree.map((n) => n.item.id)).toEqual(['a', 'b']);
		expect(tree[0].children.map((n) => n.item.id)).toEqual(['a1', 'c1']);
	});

	it('accepts a custom compare function overriding the default name sort', () => {
		const items = [item('a', null, 'Alpha'), item('b', null, 'Bravo')];
		const tree = buildTree(items, (x, y) => y.name.localeCompare(x.name));
		expect(tree.map((n) => n.item.id)).toEqual(['b', 'a']);
	});
});

describe('flattenTree', () => {
	it('walks parents before children, depth-first', () => {
		const items = [
			item('root', null, 'Root'),
			item('child-b', 'root', 'B'),
			item('child-a', 'root', 'A'),
			item('grandchild', 'child-a', 'GC')
		];
		const tree = buildTree(items);
		const flat = flattenTree(tree);
		expect(flat.map((n) => n.item.id)).toEqual(['root', 'child-a', 'grandchild', 'child-b']);
	});
});

describe('flattenVisible', () => {
	const items = [
		item('root', null, 'Root'),
		item('child', 'root', 'Child'),
		item('grandchild', 'child', 'Grandchild')
	];
	const tree = buildTree(items);

	it('collapses everything when nothing is expanded', () => {
		expect(flattenVisible(tree, new Set()).map((n) => n.item.id)).toEqual(['root']);
	});

	it('descends one level per expanded ancestor', () => {
		expect(flattenVisible(tree, new Set(['root'])).map((n) => n.item.id)).toEqual(['root', 'child']);
		expect(flattenVisible(tree, new Set(['root', 'child'])).map((n) => n.item.id)).toEqual([
			'root',
			'child',
			'grandchild'
		]);
	});
});

describe('descendantIds', () => {
	it('includes the id itself plus every transitive descendant', () => {
		const items = [
			item('root', null, 'Root'),
			item('child', 'root', 'Child'),
			item('grandchild', 'child', 'Grandchild'),
			item('sibling', null, 'Sibling')
		];
		expect(descendantIds(items, 'root')).toEqual(new Set(['root', 'child', 'grandchild']));
	});

	it('terminates instead of looping on a malformed parent_id cycle', () => {
		const items = [item('a', 'b', 'A'), item('b', 'a', 'B')];
		expect(descendantIds(items, 'a')).toEqual(new Set(['a', 'b']));
	});
});
