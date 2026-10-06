<script lang="ts">
	import { get } from 'svelte/store';
	import { tabsStore } from '$lib/stores/tabs';
	import { toasts } from '$lib/stores/toast';
	import { cancelGrid, getActiveGrid, retryFailed, turnOffCompare } from '../compareStore.svelte';
	import CompareGrid from './CompareGrid.svelte';
	import GridCellViewer from './GridCellViewer.svelte';
	import GridExport from './GridExport.svelte';
	import { cellFormPatch } from './gridModel';

	let { tabId }: { tabId: string } = $props();

	let grid = $derived(getActiveGrid(tabId));
	let viewIndex = $state<number | null>(null);
	let exporting = $state(false);
	let durations = $state(new Map<string, number>());

	let viewing = $derived(grid && viewIndex !== null && viewIndex < grid.cells.length ? viewIndex : null);

	function useSettings(index: number) {
		if (!grid) return;
		const { patch, skipped } = cellFormPatch(grid, grid.cells[index]);
		const tab = get(tabsStore).tabs.find((candidate) => candidate.id === tabId);
		if (!tab) return;
		tabsStore.updateTab(tabId, { formData: { ...(tab.formData ?? {}), ...patch } });
		turnOffCompare(tabId);
		viewIndex = null;
		if (skipped.length > 0) toasts.info(`${skipped.join(', ')} can't be copied into the form`);
		else toasts.success('Cell settings are in the form');
	}
</script>

{#if grid}
	<div class="w-full min-w-0 max-w-full" data-testid="compare-host">
		<CompareGrid
			{grid}
			bind:durations
			selectedIndex={viewing}
			onOpenCell={(index) => (viewIndex = index)}
			onCancelAll={() => void cancelGrid(tabId)}
			onRetryFailed={() => void retryFailed(tabId)}
		/>
	</div>

	{#if viewing !== null}
		<GridCellViewer
			{grid}
			index={viewing}
			seconds={grid.cells[viewing].generationId ? (durations.get(grid.cells[viewing].generationId as string) ?? null) : null}
			onIndex={(index) => (viewIndex = index)}
			onClose={() => (viewIndex = null)}
			onUse={useSettings}
			onExport={() => (exporting = true)}
			onRetryFailed={() => void retryFailed(tabId)}
		/>
	{/if}

	{#if exporting}
		<GridExport {grid} seconds={durations} onClose={() => (exporting = false)} />
	{/if}
{/if}
