<script lang="ts">
	/**
	 * Recursive renderer for the generic argument fallback tree (see
	 * `buildArgumentTree` in `$lib/chat/approvalPreview`) — the dock's last
	 * resort when a pending approval has no typed `preview` at all. Every
	 * value renders as a real, typed leaf; objects/arrays are disclosures
	 * that always carry a content preview, never a bare "object"/"N items".
	 * Follows the `<svelte:self>` recursion idiom already used by
	 * JsonTreeNode.svelte.
	 */
	import type { ArgTreeNode } from '$lib/chat/approvalPreview';

	export let nodes: ArgTreeNode[] = [];

	function toggleOpen(node: ArgTreeNode) {
		node.open = !node.open;
		nodes = nodes;
	}

	function toggleItems(node: ArgTreeNode) {
		node.itemsExpanded = !node.itemsExpanded;
		nodes = nodes;
	}

	function valueClass(node: ArgTreeNode): string {
		if (node.kind === 'boolean') return node.display === 'true' ? 'text-success' : 'text-fg-disabled';
		if (node.kind === 'number') return 'text-fg tabular-nums';
		if (node.kind === 'null') return 'text-fg-disabled';
		return 'text-fg';
	}
</script>

{#each nodes as node (node.key)}
	{#if node.kind === 'object' || node.kind === 'array'}
		<div class="py-0.5">
			<button
				type="button"
				class="flex items-center gap-1.5 text-left font-mono text-xs text-fg-subtle hover:text-fg-muted transition-colors"
				aria-expanded={node.open}
				on:click={() => toggleOpen(node)}
			>
				<svg
					class="w-2.5 h-2.5 flex-shrink-0 transition-transform {node.open ? 'rotate-90' : ''}"
					fill="none"
					stroke="currentColor"
					viewBox="0 0 24 24"
					aria-hidden="true"
				>
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 6l6 6-6 6" />
				</svg>
				<span>
					{node.key}{#if node.kind === 'array'}<span class="text-fg-disabled"> ({node.count})</span>{/if}
				</span>
			</button>

			{#if node.open}
				<div class="ml-3.5 pl-3 border-l border-line mt-0.5 mb-1">
					{#if node.kind === 'array'}
						{#if node.children?.length}
							{#if node.itemsExpanded}
								<svelte:self nodes={node.children} />
								{#if node.children.length > 1}
									<button
										type="button"
										class="mt-0.5 text-2xs font-semibold text-signal hover:text-signal/80 transition-colors"
										on:click={() => toggleItems(node)}
									>
										Show less
									</button>
								{/if}
							{:else}
								<div class="font-mono text-xs text-fg-disabled py-0.5">{node.preview}</div>
								{#if node.children.length > 1}
									<button
										type="button"
										class="mt-0.5 text-2xs font-semibold text-signal hover:text-signal/80 transition-colors"
										on:click={() => toggleItems(node)}
									>
										+{node.children.length - 1} more {node.key}
									</button>
								{/if}
							{/if}
						{/if}
					{:else}
						<svelte:self nodes={node.children || []} />
					{/if}
				</div>
			{/if}
		</div>
	{:else}
		<div class="py-0.5 flex items-start gap-2 font-mono text-xs">
			<span class="text-fg-subtle flex-shrink-0 min-w-[7rem]">{node.key}</span>
			<span class="break-words {valueClass(node)}">{node.display}</span>
		</div>
	{/if}
{/each}
