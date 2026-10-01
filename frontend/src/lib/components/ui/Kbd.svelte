<script lang="ts">
	let { keys, size = 'sm' }: { keys: string | string[]; size?: 'sm' | 'md' } = $props();

	let keyList = $derived(Array.isArray(keys) ? keys : [keys]);
	let textClass = $derived(size === 'md' ? 'text-xs' : 'text-2xs');
</script>

<span class="inline-flex items-center gap-1 font-mono {textClass} text-fg-muted" aria-hidden="true">
	{#each keyList as key, i (key + i)}
		{#if i > 0}<span aria-hidden="true">+</span>{/if}
		<kbd class="px-1.5 py-0.5 {textClass} font-mono rounded-sm bg-surface-3 text-fg-muted border border-line-hover">
			{key}
		</kbd>
	{/each}
</span>
