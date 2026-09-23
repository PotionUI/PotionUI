<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import type { FilterChip } from './librarySection';

	let {
		chips,
		onRemoveChip,
		onClearAll,
		loadedCount,
		total
	}: {
		chips: readonly FilterChip[];
		onRemoveChip: (key: string) => void;
		onClearAll: () => void;
		loadedCount: number;
		total: number;
	} = $props();
</script>

{#if chips.length > 0}
	<div class="flex flex-shrink-0 flex-wrap items-center gap-1.5 rounded-lg border border-line bg-surface-1 px-4 py-2">
		{#each chips as chip (chip.key)}
			<span class="inline-flex h-6 items-center gap-1.5 rounded border border-signal/28 bg-signal/10 px-2 font-mono text-xs text-signal">
				{chip.label}
				<button
					type="button"
					aria-label={`Remove filter ${chip.label}`}
					class="opacity-70 hover:opacity-100"
					onclick={() => onRemoveChip(chip.key)}
				>
					<Icon name="close" className="h-2.5 w-2.5" />
				</button>
			</span>
		{/each}
		<button type="button" class="text-xs text-fg-subtle hover:text-fg" onclick={onClearAll}>Clear all</button>
		<span class="ml-auto font-mono text-xs tabular-nums text-fg-subtle">{loadedCount} of {total}</span>
	</div>
{/if}
