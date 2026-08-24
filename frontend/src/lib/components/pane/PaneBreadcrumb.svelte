<script lang="ts">
	let {
		segments,
		onNavigate
	}: {
		segments: { id: string; label: string }[];
		onNavigate: (id: string, index: number) => void;
	} = $props();
</script>

<div class="px-3 py-1.5 border-b border-line font-mono text-2xs overflow-x-auto whitespace-nowrap">
	{#each segments as segment, i (segment.id)}
		{#if i > 0}
			<span class="text-fg-subtle mx-1">&rsaquo;</span>
		{/if}
		{#if i === segments.length - 1}
			<span class="text-fg">{segment.label}</span>
		{:else}
			<button type="button" class="text-fg-subtle hover:text-fg" onclick={() => onNavigate(segment.id, i)}>
				{segment.label}
			</button>
		{/if}
	{/each}
</div>
