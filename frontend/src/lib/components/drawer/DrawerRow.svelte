<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		title,
		meta,
		actions,
		onSelect,
		rowId,
		highlight = false
	}: {
		title: Snippet;
		meta?: Snippet;
		actions?: Snippet;
		onSelect: () => void;
		rowId: string;
		highlight?: boolean;
	} = $props();
</script>

<li class="item mx-2 rounded" data-row-id={rowId}>
	<div class="row relative flex min-h-[52px] items-center gap-0.5 rounded pr-1 hover:bg-surface-2 {highlight ? 'bg-signal/10' : ''}" data-highlight={highlight ? '' : undefined}>
		<button
			type="button"
			class="rmain flex min-h-[52px] min-w-0 flex-1 flex-col justify-center rounded px-3 text-left"
			data-nav
			onclick={onSelect}
		>
			<span class="block min-w-0 truncate text-sm text-fg">{@render title()}</span>
			{#if meta}{@render meta()}{/if}
		</button>
		{@render actions?.()}
	</div>
</li>

<style>
	.rmain:focus-visible {
		outline: none;
		background: rgb(var(--surface-2));
		box-shadow: inset 2px 0 0 rgb(var(--signal));
	}
</style>
