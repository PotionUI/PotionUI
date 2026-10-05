<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { limits, refreshLimits } from '../store';
	import { formatAmount, percentOf } from '../limitView';
	import LimitBar from './LimitBar.svelte';

	onMount(() => {
		void refreshLimits();
	});

	let row = $derived($limits.find((r) => r.format === 'bytes' && !r.resets_at) ?? null);
	const surface = { ok: 'border-line', warn: 'border-warning/40', full: 'border-danger/40' } as const;
	const noteTone = { ok: 'text-fg-subtle', warn: 'text-warning', full: 'text-danger' } as const;
</script>

{#if row}
	{@const percent = percentOf(row)}
	<div class="m-2 rounded-lg border bg-surface-1 px-3 py-2.5 {surface[row.state]}" data-history-usage>
		<div class="flex items-baseline justify-between gap-2">
			<span class="text-2xs font-medium uppercase tracking-wide text-fg-subtle">{row.label}</span>
			<span class="font-mono tabular-nums text-xs text-fg">{formatAmount(row)}</span>
		</div>
		<LimitBar class="mt-2" {percent} state={row.state} label={row.label} />
		<div class="mt-2 flex items-center justify-between text-xs">
			<span class="font-mono tabular-nums {noteTone[row.state]}">
				{row.state === 'full' ? 'Full' : row.state === 'warn' ? `${percent}% - getting full` : `${percent}% used`}
			</span>
			<button type="button" class="text-signal hover:underline" onclick={() => goto('/settings#plan')}>
				Details
			</button>
		</div>
	</div>
{/if}
