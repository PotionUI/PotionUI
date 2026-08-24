<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '../Icon.svelte';

	let {
		title,
		description,
		icon = 'image',
		compact = false,
		actions
	}: {
		title: string;
		description?: string;
		icon?: string;
		compact?: boolean;
		actions?: Snippet;
	} = $props();
</script>

<div class="dot-grid flex flex-col items-center justify-center text-center rounded-lg border border-line bg-surface-1 {compact ? 'px-4 py-8' : 'px-6 py-14'}">
	<Icon name={icon} className="w-9 h-9 text-fg-subtle mb-3" strokeWidth={1.5} />
	<h2 class="text-sm font-semibold text-fg">{title}</h2>
	{#if description}<p class="text-sm text-fg-muted mt-1 max-w-md">{description}</p>{/if}
	{#if actions}<div class="flex flex-wrap items-center justify-center gap-2 mt-4">{@render actions()}</div>{/if}
</div>
