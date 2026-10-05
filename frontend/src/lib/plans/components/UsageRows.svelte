<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import type { LimitRow } from '../meApi';
	import { formatAmount, leftNote, percentOf, resetNote } from '../limitView';
	import LimitBar from './LimitBar.svelte';

	let { rows, now }: { rows: LimitRow[]; now: number } = $props();

	const icons = { bytes: 'database', count: 'bolt', percent: 'calendar' } as const;
	const amountTone = {
		ok: 'text-fg',
		warn: 'text-warning',
		full: 'text-danger'
	} as const;
</script>

{#if rows.length === 0}
	<p class="text-sm text-fg-muted">No limits on your account</p>
{:else}
	<ul class="divide-y divide-line">
		{#each rows as row (row.kind)}
			{@const note = resetNote(row, now) ?? leftNote(row)}
			<li class="flex items-center gap-3 py-3" data-limit-row={row.kind}>
				<Icon name={icons[row.format]} className="w-4 h-4 shrink-0 text-fg-subtle" strokeWidth={1.5} />
				<p class="text-sm font-medium text-fg min-w-0 flex-1 truncate">{row.label}</p>
				<LimitBar
					class="hidden sm:block w-40 md:w-56 shrink-0"
					percent={percentOf(row)}
					state={row.state}
					label={row.label}
				/>
				<div class="text-right shrink-0 min-w-[7.5rem]">
					<p class="font-mono tabular-nums text-sm {amountTone[row.state]}">{formatAmount(row)}</p>
					{#if note}
						<p class="text-xs text-fg-subtle">{note}</p>
					{/if}
				</div>
			</li>
		{/each}
	</ul>
{/if}
