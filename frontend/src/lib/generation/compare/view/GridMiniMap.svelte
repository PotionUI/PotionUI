<script lang="ts">
	import type { ActiveGrid } from '../compareStore.svelte';
	import { cellAxisSummary, cellStatusLabel } from './gridModel';

	let {
		grid,
		selectedIndex = null,
		compact = false,
		onSelect
	}: {
		grid: Pick<ActiveGrid, 'cells' | 'cols' | 'rows'>;
		selectedIndex?: number | null;
		compact?: boolean;
		onSelect?: (index: number) => void;
	} = $props();

	function tone(status: string): string {
		if (status === 'failed' || status === 'deleted') return 'border-danger/40 bg-danger/10';
		if (status === 'running') return 'border-signal/40 bg-signal/10';
		if (status === 'queued') return 'border-dashed border-line-strong bg-surface-1';
		return 'border-line bg-surface-1';
	}
</script>

<div
	class="grid gap-1"
	style="grid-template-columns: repeat({grid.cols}, minmax(0, 1fr))"
	role="group"
	aria-label="Grid map"
	data-testid="grid-minimap"
>
	{#each grid.cells as cell, index (index)}
		<button
			type="button"
			data-testid="grid-minimap-cell"
			data-cell-state={cell.status}
			aria-label={`Cell ${index + 1}, ${cellAxisSummary(cell.axisValues) || 'no axis values'}, ${cellStatusLabel(cell.status).toLowerCase()}`}
			aria-current={selectedIndex === index ? 'true' : undefined}
			class="relative overflow-hidden rounded-sm border transition-colors {tone(cell.status)} {compact
				? 'aspect-square'
				: 'aspect-[4/3]'} {selectedIndex === index ? 'ring-1 ring-signal' : 'hover:border-line-hover'}"
			onclick={() => onSelect?.(index)}
		>
			{#if cell.status === 'completed' && cell.thumbnailUrl}
				<img src={cell.thumbnailUrl} alt="" class="h-full w-full object-cover" loading="lazy" />
			{/if}
		</button>
	{/each}
</div>
