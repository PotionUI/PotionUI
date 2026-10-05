<script lang="ts">
	import type { CompareAxisValue } from '$lib/generation/compare/types';

	let { values, limit = 5 }: { values: CompareAxisValue[]; limit?: number } = $props();

	let shown = $derived(values.slice(0, limit));
	let hidden = $derived(Math.max(0, values.length - limit));
</script>

{#if values.length > 0}
	<div class="flex flex-wrap gap-1.5" data-testid="axis-value-preview">
		{#each shown as entry, index (index)}
			<span class="rounded border border-signal/40 bg-signal/10 px-2 py-0.5 font-mono text-xs tabular-nums text-signal">{entry.label}</span>
		{/each}
		{#if hidden > 0}
			<span class="rounded border border-line px-2 py-0.5 font-mono text-xs tabular-nums text-fg-subtle">+{hidden}</span>
		{/if}
	</div>
{/if}
