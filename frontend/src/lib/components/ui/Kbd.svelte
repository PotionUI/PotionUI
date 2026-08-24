<script lang="ts">
	let { keys }: { keys: string | string[] } = $props();

	let keyList = $derived(Array.isArray(keys) ? keys : [keys]);
</script>

<span class="inline-flex items-center gap-1 font-mono text-2xs text-fg-muted">
	{#each keyList as key, i (key + i)}
		{#if i > 0}<span aria-hidden="true">+</span>{/if}
		<kbd class="px-1.5 py-0.5 text-2xs font-mono rounded-sm bg-surface-3 text-fg-muted border border-line-hover">
			{key}
		</kbd>
	{/each}
</span>
