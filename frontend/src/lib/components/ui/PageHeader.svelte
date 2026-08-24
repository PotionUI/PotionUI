<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		title,
		description,
		sticky = true,
		wrap = false,
		left,
		actions,
		children
	}: {
		title?: string;
		description?: string;
		sticky?: boolean;
		wrap?: boolean;
		left?: Snippet;
		actions?: Snippet;
		children?: Snippet;
	} = $props();

	let heightClasses = $derived(wrap ? 'min-h-header py-3 flex-wrap' : 'min-h-header py-3');
	let classes = $derived(
		`bg-surface-1 border-b border-line px-4 sm:px-6 flex items-center justify-between gap-3 sm:gap-4 flex-shrink-0 ${heightClasses} ${
			sticky ? 'sticky top-0 z-30' : ''
		}`
	);
</script>

<header class={classes}>
	{#if children}
		{@render children()}
	{:else}
		<div class="flex items-center gap-4 min-w-0">
			{#if title}
				<div class="min-w-0">
					<h1 class="text-lg font-semibold text-fg truncate">{title}</h1>
					{#if description}<p class="text-xs text-fg-muted mt-0.5 truncate">{description}</p>{/if}
				</div>
			{/if}
			{@render left?.()}
		</div>
		{#if actions}
			<div class="flex items-center justify-end gap-2 flex-wrap">
				{@render actions()}
			</div>
		{/if}
	{/if}
</header>
