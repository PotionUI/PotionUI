<script lang="ts" generics="T extends CollectionLike">
	import Icon from '$lib/components/Icon.svelte';
	import { buildTree, flattenTree, type PaneTreeNode } from '$lib/components/pane';
	import type { CollectionLike } from './types';

	let {
		collections,
		blockedIds = new Set<string>(),
		disabled = false,
		showRoot = false,
		rootLabel = 'Top level',
		emptyMessage = 'No collections yet',
		class: className = '',
		onSelect
	}: {
		collections: T[];
		// Ids the caller won't let this pick land on (e.g. a "Move to…" batch
		// excluding the selected folders and their own descendants). Add-style
		// pickers leave this empty - every collection is a valid target.
		blockedIds?: Set<string>;
		disabled?: boolean;
		// The move picker offers an explicit "un-parent" target; add pickers
		// have no such concept (there's no "no collection" to add into).
		showRoot?: boolean;
		rootLabel?: string;
		emptyMessage?: string;
		class?: string;
		onSelect: (id: string | null) => void;
	} = $props();

	let targets = $derived(
		flattenTree(buildTree(collections)).filter((n: PaneTreeNode<T>) => !blockedIds.has(n.item.id))
	);
</script>

<div class="overflow-y-auto p-1 {className}">
	{#if showRoot}
		<button
			class="w-full text-left px-2 py-1.5 rounded text-sm text-fg-muted hover:text-fg hover:bg-surface-2 flex items-center gap-1.5 disabled:opacity-50"
			{disabled}
			onclick={() => onSelect(null)}
			role="menuitem"
		>
			<span class="text-fg-subtle">／</span> {rootLabel}
		</button>
	{/if}
	{#each targets as target (target.item.id)}
		<button
			class="w-full text-left pr-2 py-1.5 rounded text-sm text-fg-muted hover:text-fg hover:bg-surface-2 flex items-center gap-1.5 disabled:opacity-50"
			style="padding-left: {target.depth * 12 + 8}px"
			{disabled}
			onclick={() => onSelect(target.item.id)}
			role="menuitem"
		>
			<Icon name="folder" className="w-3.5 h-3.5 text-fg-subtle flex-shrink-0" />
			<span class="truncate">{target.item.name}</span>
		</button>
	{/each}
	{#if targets.length === 0}
		<div class="px-2 py-3 text-center text-xs text-fg-subtle">{emptyMessage}</div>
	{/if}
</div>
