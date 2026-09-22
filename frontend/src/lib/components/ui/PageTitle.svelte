<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		title,
		description,
		count,
		countLabel,
		leading,
		children
	}: {
		title: string;
		description?: string;
		count?: string | number;
		countLabel?: string;
		leading?: Snippet;
		children?: Snippet;
	} = $props();
</script>

<div class="flex items-baseline gap-3 flex-shrink-0 min-w-0">
	{#if leading}
		<span class="flex flex-shrink-0 self-center">{@render leading()}</span>
	{/if}
	<div class="min-w-0">
		<h1 class="text-sm font-semibold text-fg truncate">{title}</h1>
		{#if description}
			<p class="hidden sm:block text-xs text-fg-muted truncate">{description}</p>
		{/if}
	</div>
	{#if count !== undefined}
		<span class="font-mono tabular-nums text-xs uppercase tracking-[0.07em] text-fg-subtle whitespace-nowrap">
			{count}{#if countLabel}{' '}{countLabel}{/if}
		</span>
	{/if}
	{@render children?.()}
</div>
