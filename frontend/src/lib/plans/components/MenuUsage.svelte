<script lang="ts">
	import type { LimitRow } from '../meApi';
	import { closestLimit, formatAmount, percentOf } from '../limitView';
	import LimitBar from './LimitBar.svelte';

	let { rows, onOpen }: { rows: LimitRow[]; onOpen: () => void } = $props();

	let row = $derived(closestLimit(rows));
	let others = $derived(rows.length - 1);
	const surface = {
		ok: 'border-line',
		warn: 'border-warning/40',
		full: 'border-danger/40'
	} as const;
</script>

{#if row}
	<button
		type="button"
		role="menuitem"
		onclick={onOpen}
		class="block w-full text-left rounded border bg-surface-1 px-2.5 py-2 hover:bg-surface-3 transition-colors {surface[row.state]}"
		data-menu-usage
	>
		<span class="flex items-baseline justify-between gap-2">
			<span class="text-2xs font-medium uppercase tracking-wide text-fg-subtle truncate">{row.label}</span>
			<span class="font-mono tabular-nums text-xs text-fg">{formatAmount(row)}</span>
		</span>
		<LimitBar class="mt-1.5" percent={percentOf(row)} state={row.state} label={row.label} />
		<span class="mt-1.5 flex items-center justify-between text-2xs text-fg-subtle">
			<span class="font-mono tabular-nums">{others > 0 ? `+${others} ${others === 1 ? 'limit' : 'limits'}` : ''}</span>
			<span class="text-signal">Plan</span>
		</span>
	</button>
{/if}
