<script lang="ts">
	import { onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Spinner } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import { tabsStore } from '$lib/stores/tabs';
	import { toasts } from '$lib/stores/toast';
	import { historyStore } from '$lib/stores/history';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import { buildHistoryReuseTabData, buildReuseTabTitle } from '$lib/utils/historyReuse';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import type { ActiveGrid } from '$lib/generation/compare/compareStore.svelte';
	import CompareGrid from '$lib/generation/compare/view/CompareGrid.svelte';
	import GridCellViewer from '$lib/generation/compare/view/GridCellViewer.svelte';
	import GridExport from '$lib/generation/compare/view/GridExport.svelte';
	import { fetchGrid, openGridId, retryGridFailed } from '$lib/generation/compare/view/gridApi';
	import { gridDimensions, gridProgress, gridTitle } from '$lib/generation/compare/view/gridModel';

	let { gridId, onClose }: { gridId: string; onClose: () => void } = $props();

	const POLL_MS = 2500;

	let grid = $state<ActiveGrid | null>(null);
	let failure = $state<string | null>(null);
	let viewIndex = $state<number | null>(null);
	let exporting = $state(false);
	let timer: ReturnType<typeof setTimeout> | null = null;
	let stopped = false;

	let viewing = $derived(grid && viewIndex !== null && viewIndex < grid.cells.length ? viewIndex : null);

	async function load() {
		try {
			grid = await fetchGrid(gridId);
			failure = null;
		} catch (error) {
			failure = getErrorMessage(error);
			logger.error('Failed to load the grid', failure);
		}
		if (stopped) return;
		if (grid && gridProgress(grid).active) timer = setTimeout(load, POLL_MS);
	}

	$effect(() => {
		void gridId;
		stopped = false;
		grid = null;
		void load();
		return () => {
			stopped = true;
			if (timer) clearTimeout(timer);
		};
	});

	onDestroy(() => {
		stopped = true;
		if (timer) clearTimeout(timer);
	});

	async function retry() {
		try {
			grid = await retryGridFailed(gridId);
			if (!stopped && grid && gridProgress(grid).active) {
				if (timer) clearTimeout(timer);
				timer = setTimeout(load, POLL_MS);
			}
		} catch (error) {
			toasts.error(getErrorMessage(error));
		}
	}

	function reuse(generation: GenerationHistoryItem) {
		if (!generation.preset_id) return;
		const { tabData } = buildHistoryReuseTabData(generation);
		tabsStore.addTabWithData(buildReuseTabTitle('Reused', generation, () => null), tabData);
		onClose();
		goto('/generate');
	}

	async function useSettings(index: number) {
		const id = grid?.cells[index]?.generationId;
		if (!id) return;
		try {
			const response = await api.getGenerationById(id, true, true);
			if (response.success && response.data) reuse(response.data as GenerationHistoryItem);
		} catch (error) {
			toasts.error(getErrorMessage(error));
		}
	}

	function close() {
		historyStore.setSelectedGeneration(null);
		openGridId.set(null);
		onClose();
	}
</script>

<BaseModal isOpen={true} title="Compare grid" size="full" closeable={viewing === null && !exporting} on:close={close}>
	<svelte:fragment slot="headerIcon">
		<div class="flex h-[30px] w-[30px] flex-shrink-0 items-center justify-center rounded border border-line bg-surface-2">
			<Icon name="grid" className="h-4 w-4 text-fg-muted" />
		</div>
	</svelte:fragment>
	<svelte:fragment slot="header">
		{#if grid}
			<span class="font-mono text-2xs tabular-nums text-fg-subtle" data-testid="history-grid-dims">
				{gridTitle(grid.config)} · {gridDimensions(grid)}
			</span>
		{/if}
	</svelte:fragment>

	<div class="h-full min-h-0 overflow-auto p-4" data-testid="history-grid-view">
		{#if grid}
			<CompareGrid
				{grid}
				overview
				selectedIndex={viewing}
				onOpenCell={(index) => (viewIndex = index)}
				onRetryFailed={retry}
			/>
		{:else if failure}
			<p class="text-sm text-danger">{failure}</p>
		{:else}
			<div class="flex h-full items-center justify-center"><Spinner size="lg" /></div>
		{/if}
	</div>
</BaseModal>

{#if grid && viewing !== null}
	<GridCellViewer
		{grid}
		index={viewing}
		onIndex={(index) => (viewIndex = index)}
		onClose={() => (viewIndex = null)}
		onUse={useSettings}
		onExport={() => (exporting = true)}
		onRetryFailed={retry}
		onReuse={reuse}
	/>
{/if}

{#if grid && exporting}
	<GridExport {grid} onClose={() => (exporting = false)} />
{/if}
