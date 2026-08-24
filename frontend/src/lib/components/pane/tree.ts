// Generalized from utils/collectionTree.ts to work over any TreeItem-shaped
// list, not just collections — the pane family (library sidebar, category
// tree, docs sidebar, …) all browse a flat, nestable parent_id list the same
// way.
import type { PaneTreeNode, TreeItem } from './types';

function defaultCompare<T>(a: T, b: T): number {
	const an = (a as { name?: unknown }).name;
	const bn = (b as { name?: unknown }).name;
	if (typeof an === 'string' && typeof bn === 'string') return an.localeCompare(bn);
	return 0;
}

// Roots are items whose parent_id is null/undefined (or points at a missing
// parent, which we defensively treat as a root so nothing is ever hidden).
// Array.prototype.sort is stable, so items without a `name` (defaultCompare
// returns 0 for them) keep their original input order.
export function buildTree<T extends TreeItem>(
	items: T[],
	compare: (a: T, b: T) => number = defaultCompare
): PaneTreeNode<T>[] {
	const ids = new Set(items.map((i) => i.id));

	const childrenOf = (parentId: string | null, depth: number): PaneTreeNode<T>[] =>
		items
			.filter((item) => {
				const pid = item.parent_id ?? null;
				if (parentId === null) return pid === null || !ids.has(pid);
				return pid === parentId;
			})
			.sort(compare)
			.map((item) => ({
				item,
				children: childrenOf(item.id, depth + 1),
				depth
			}));

	return childrenOf(null, 0);
}

// DFS order, parents before children.
export function flattenTree<T>(nodes: PaneTreeNode<T>[]): PaneTreeNode<T>[] {
	const out: PaneTreeNode<T>[] = [];
	const walk = (list: PaneTreeNode<T>[]) => {
		for (const n of list) {
			out.push(n);
			walk(n.children);
		}
	};
	walk(nodes);
	return out;
}

// Same DFS walk, but only descends into a node's children when the node's id
// is in `expanded` — the flat row list a virtualized/keyboard-navigable pane
// body renders.
export function flattenVisible<T extends TreeItem>(
	nodes: PaneTreeNode<T>[],
	expanded: ReadonlySet<string>
): PaneTreeNode<T>[] {
	const out: PaneTreeNode<T>[] = [];
	const walk = (list: PaneTreeNode<T>[]) => {
		for (const n of list) {
			out.push(n);
			if (n.children.length > 0 && expanded.has(n.item.id)) {
				walk(n.children);
			}
		}
	};
	walk(nodes);
	return out;
}

// The id plus every descendant id under it. Used to keep a "Move to…" picker
// from offering a target that would create a cycle. The result-set membership
// check also makes this cycle-safe against malformed parent_id loops.
export function descendantIds<T extends TreeItem>(items: T[], id: string): Set<string> {
	const result = new Set<string>([id]);
	const stack = [id];
	while (stack.length) {
		const current = stack.pop() as string;
		for (const item of items) {
			if ((item.parent_id ?? null) === current && !result.has(item.id)) {
				result.add(item.id);
				stack.push(item.id);
			}
		}
	}
	return result;
}
