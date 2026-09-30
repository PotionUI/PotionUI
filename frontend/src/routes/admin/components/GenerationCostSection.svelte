<script lang="ts">
	import { DetailSection } from '$lib/components/detail';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge } from '$lib/components/ui';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { costSourceLabel, type GenerationCost } from '$lib/utils/cloudCost';
	import { taskLabel } from './cloudCatalog';
	import CostValue from './CostValue.svelte';

	let {
		cost
	}: {
		cost: GenerationCost;
	} = $props();

	function itemLabel(detail: Record<string, unknown>, index: number): string {
		const task = detail?.task;
		return typeof task === 'string' && task ? taskLabel(task) : `Job ${index + 1}`;
	}
</script>

<DetailSection label="Cost">
	{#snippet headerExtra()}
		{#if cost.source}
			<Badge size="sm">{costSourceLabel(cost.source)}</Badge>
		{/if}
	{/snippet}
	<div class="space-y-3" data-generation-cost>
		<div class="text-xl font-semibold"><CostValue cost={cost} /></div>
		{#if cost.items.length > 0}
			<ul class="divide-y divide-line rounded border border-line bg-surface-2/40">
				{#each cost.items as item, index (item.id)}
					<li class="flex items-center gap-2 px-2.5 py-1.5" data-cost-item>
						<div class="min-w-0 flex-1">
							<Tooltip text={[item.backend_id && `Backend ${item.backend_id}`, item.model_id && `Model ${item.model_id}`].filter(Boolean).join(' · ') || item.id} wrapperClass="block min-w-0">
								<span class="block truncate text-sm text-fg">{itemLabel(item.detail, index)}</span>
							</Tooltip>
							<span class="block text-xs text-fg-subtle">{costSourceLabel(item.source)} · {timeAgo(item.created_at)}</span>
						</div>
						<CostValue cost={{ amount_usd: item.amount_usd, source: item.source, entries: 1, unpriced: item.amount_usd === null ? 1 : 0 }} class="text-sm" />
					</li>
				{/each}
			</ul>
		{/if}
	</div>
</DetailSection>
