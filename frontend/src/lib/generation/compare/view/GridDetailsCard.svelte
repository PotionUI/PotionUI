<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/ui';
	import type { ActiveGrid } from '../compareStore.svelte';
	import GridMiniMap from './GridMiniMap.svelte';
	import { cellAxisSummary } from './gridModel';

	let {
		grid,
		index,
		openLabel = 'Open grid',
		onOpenGrid,
		onSelect
	}: {
		grid: ActiveGrid;
		index: number;
		openLabel?: string;
		onOpenGrid?: () => void;
		onSelect?: (index: number) => void;
	} = $props();

	let cell = $derived(grid.cells[index]);
	let summary = $derived(cell ? cellAxisSummary(cell.axisValues) : '');
</script>

<div class="overflow-hidden rounded-lg border border-signal/30 bg-surface-2" data-testid="grid-details-card">
	<div class="flex items-center justify-between border-b border-line px-3 py-2.5">
		<div class="flex items-center gap-2">
			<Icon name="grid" className="h-4 w-4 text-signal" />
			<h3 class="text-sm font-semibold text-fg">Compare grid</h3>
		</div>
		<span class="font-mono text-2xs tabular-nums text-fg-subtle">cell {index + 1} of {grid.cells.length}</span>
	</div>
	<div class="space-y-3 p-3">
		<p class="text-sm text-fg" data-testid="grid-details-summary">
			Part of X/Y grid{summary ? ` · ${summary}` : ''}
		</p>
		<div class="flex items-end justify-between gap-3">
			<div class="w-32">
				<GridMiniMap {grid} selectedIndex={index} compact {onSelect} />
			</div>
			<Button variant="secondary" size="xs" onclick={() => onOpenGrid?.()}>{openLabel}</Button>
		</div>
	</div>
</div>
