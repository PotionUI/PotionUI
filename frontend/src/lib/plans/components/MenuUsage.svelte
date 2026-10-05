<script lang="ts">
	import type { LimitRow } from '../meApi';
	import { formatBytes } from '../format';
	import { closestLimit, formatAmount, percentOf } from '../limitView';
	import LimitBar from './LimitBar.svelte';

	let {
		rows,
		storageBytes = null,
		onOpen
	}: { rows: LimitRow[]; storageBytes?: number | null; onOpen: () => void } = $props();

	let counted = $derived(rows.filter((candidate) => !candidate.perItem));
	let row = $derived(closestLimit(counted));
	let others = $derived(counted.length - 1);
	let storageRow = $derived(counted.find((candidate) => candidate.kind === 'storage_bytes'));
	let storageLine = $derived.by(() => {
		if (row && row.kind === 'storage_bytes') return null;
		if (storageRow) return formatAmount(storageRow);
		return storageBytes === null ? null : `${formatBytes(storageBytes)} · no limit`;
	});
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
{#if storageLine}
	<button
		type="button"
		role="menuitem"
		onclick={onOpen}
		class="flex w-full items-baseline justify-between gap-2 rounded px-2.5 py-2 text-left hover:bg-surface-3 transition-colors {row ? 'mt-1' : ''}"
		data-menu-storage
	>
		<span class="text-2xs font-medium uppercase tracking-wide text-fg-subtle">Storage</span>
		<span class="font-mono tabular-nums text-xs text-fg-muted">{storageLine}</span>
	</button>
{/if}
