<script lang="ts">
	import { DetailSection } from '$lib/components/detail';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { formatCount } from '$lib/utils/format';
	import {
		formatUsd,
		formatUsdExact,
		sortByAmountDesc,
		spendIsEmpty,
		spendNote,
		type CloudSpend,
		type SpendBucket
	} from '$lib/utils/cloudCost';
	import CostValue from './CostValue.svelte';

	let {
		spend
	}: {
		spend: CloudSpend | null;
	} = $props();

	type Row = { key: string; name: string; bucket: SpendBucket };

	const byBackend = $derived<Row[]>(
		sortByAmountDesc(spend?.by_backend ?? []).map((row) => ({
			key: row.backend_id ?? 'none',
			name: row.backend_name || 'Removed backend',
			bucket: row
		}))
	);
	const byModel = $derived<Row[]>(
		sortByAmountDesc(spend?.by_model ?? []).map((row) => ({
			key: row.model_id ?? 'none',
			name: row.model || 'Unknown model',
			bucket: row
		}))
	);
</script>

{#snippet table(title: string, nameHeader: string, rows: Row[])}
	<div class="min-w-0" data-spend-table={title}>
		<h4 class="mb-1.5 text-xs font-medium text-fg-subtle">{title}</h4>
		<div class="overflow-x-auto">
			<table class="w-full text-xs">
				<thead>
					<tr class="border-b border-line">
						<th class="py-1.5 px-2 text-left font-medium text-fg-subtle">{nameHeader}</th>
						<th class="py-1.5 px-2 text-right font-medium text-fg-subtle">Jobs</th>
						<th class="py-1.5 px-2 text-right font-medium text-fg-subtle">Spend</th>
					</tr>
				</thead>
				<tbody>
					{#each rows as row (row.key)}
						<tr class="border-b border-line/50 last:border-0" data-spend-row>
							<td class="py-1.5 px-2 text-fg max-w-48">
								<Tooltip text={row.name} wrapperClass="block">
									<span class="block truncate">{row.name}</span>
								</Tooltip>
							</td>
							<td class="py-1.5 px-2 text-right font-mono tabular-nums text-fg-muted">{formatCount(row.bucket.entries)}</td>
							<td class="py-1.5 px-2 text-right"><CostValue cost={row.bucket} class="text-xs" /></td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</div>
{/snippet}

{#if spend && !spendIsEmpty(spend)}
	<DetailSection label="Cloud spend">
		<div class="space-y-4" data-cloud-spend>
			<div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
				<Tooltip text={formatUsdExact(spend.total_usd)}>
					<span class="font-mono text-2xl font-semibold tabular-nums text-fg" data-spend-total>{formatUsd(spend.total_usd)}</span>
				</Tooltip>
				<span class="font-mono text-xs tabular-nums text-fg-subtle">
					{formatCount(spend.entries)} {spend.entries === 1 ? 'job' : 'jobs'}{#if spend.unpriced > 0}{' · '}{formatCount(spend.unpriced)} unpriced{/if}
				</span>
			</div>
			<p class="text-xs text-fg-muted" data-spend-note>{spendNote(spend)}</p>
			<div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
				{@render table('By backend', 'Backend', byBackend)}
				{@render table('By model', 'Model', byModel)}
			</div>
		</div>
	</DetailSection>
{/if}
