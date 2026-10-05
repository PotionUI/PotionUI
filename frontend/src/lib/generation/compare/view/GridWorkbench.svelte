<script lang="ts">
	import { get } from 'svelte/store';
	import { tabsStore } from '$lib/stores/tabs';
	import { toasts } from '$lib/stores/toast';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import { cancelGrid, getActiveGrid, retryFailed, setCompare } from '../compareStore.svelte';
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

	async function run(action: () => Promise<void>) {
		try {
			await action();
		} catch (error) {
			logger.error('Compare grid action failed', getErrorMessage(error));
			toasts.error(getErrorMessage(error));
		}
	}

	function useSettings(index: number) {
		if (!grid) return;
		const { patch, skipped } = cellFormPatch(grid, grid.cells[index]);
		const tab = get(tabsStore).tabs.find((candidate) => candidate.id === tabId);
		if (!tab) return;
		tabsStore.updateTab(tabId, { formData: { ...(tab.formData ?? {}), ...patch } });
		setCompare(tabId, { armed: false });
		viewIndex = null;
		if (skipped.length > 0) toasts.info(`${skipped.join(', ')} can't be copied into the form`);
		else toasts.success('Cell settings are in the form');
	}
</script>

{#if grid}
	<CompareGrid
		{grid}
		bind:durations
		selectedIndex={viewing}
		onOpenCell={(index) => (viewIndex = index)}
		onCancelAll={() => run(() => cancelGrid(tabId))}
		onRetryFailed={() => run(() => retryFailed(tabId))}
	/>

	{#if viewing !== null}
		<GridCellViewer
			{grid}
			index={viewing}
			seconds={grid.cells[viewing].generationId ? (durations.get(grid.cells[viewing].generationId as string) ?? null) : null}
			onIndex={(index) => (viewIndex = index)}
			onClose={() => (viewIndex = null)}
			onUse={useSettings}
			onExport={() => (exporting = true)}
			onRetryFailed={() => run(() => retryFailed(tabId))}
		/>
	{/if}

	{#if exporting}
		<GridExport {grid} seconds={durations} onClose={() => (exporting = false)} />
	{/if}
{/if}
