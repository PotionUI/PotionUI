<script lang="ts">
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { costCell, type CostSummary } from '$lib/utils/cloudCost';

	let {
		cost,
		class: className = ''
	}: {
		cost: CostSummary | null | undefined;
		class?: string;
	} = $props();

	const cell = $derived(costCell(cost));
</script>

{#if cell.kind === 'none'}
	<span class="font-mono tabular-nums text-fg-subtle {className}" data-cost="none">{cell.text}</span>
{:else}
	<Tooltip text={cell.tooltip ?? ''}>
		<span class="inline-flex items-baseline gap-1 whitespace-nowrap font-mono tabular-nums {className}" data-cost={cell.kind}>
			{#if cell.kind === 'unpriced'}
				<span class="text-fg-subtle">{cell.text}</span>
			{:else}
				<span class="text-fg">{cell.text}</span>
				{#if cell.estimate}
					<span class="text-xs text-fg-subtle" data-cost-estimate>est.</span>
				{/if}
			{/if}
		</span>
	</Tooltip>
{/if}
