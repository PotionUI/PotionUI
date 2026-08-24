<script lang="ts" generics="T extends TreeItem">
	import type { Snippet } from 'svelte';
	import type { PaneTreeNode, RowContext, TreeItem } from './types';
	import type { ExpansionState } from './expansion.svelte';

	let {
		nodes,
		expanded,
		onToggle,
		hasChildren,
		row
	}: {
		nodes: PaneTreeNode<T>[];
		expanded: ReadonlySet<string> | ExpansionState;
		onToggle: (id: string) => void;
		hasChildren?: (node: PaneTreeNode<T>) => boolean;
		row: Snippet<[RowContext<T>]>;
	} = $props();

	function nodeHasChildren(node: PaneTreeNode<T>): boolean {
		return hasChildren ? hasChildren(node) : node.children.length > 0;
	}
</script>

<!--
	Recursion via an internal named snippet (no Self import). `expanded.has(id)`
	is called inline in the markup rather than lifted into an {@const} — {@const}
	calls inside a snippet don't retrack on state changes and would freeze the
	expand/collapse state at first render.
-->
{#snippet subtree(list: PaneTreeNode<T>[])}
	{#each list as node (node.item.id)}
		{@render row({
			item: node.item,
			node,
			depth: node.depth,
			hasChildren: nodeHasChildren(node),
			expanded: expanded.has(node.item.id),
			toggle: () => onToggle(node.item.id)
		})}
		{#if expanded.has(node.item.id) && node.children.length > 0}
			{@render subtree(node.children)}
		{/if}
	{/each}
{/snippet}

<div role="tree">
	{@render subtree(nodes)}
</div>
