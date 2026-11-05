<script lang="ts">
	import { onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton, Spinner } from '$lib/components/ui';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { gridDeleteMessage } from '$lib/utils/historyDeleteCopy';
	import { api } from '$lib/services/api/index';
	import { tabsStore } from '$lib/stores/tabs';
	import { toasts } from '$lib/stores/toast';
	import { historyStore } from '$lib/stores/history';
	import { logger, getErrorMessage, getApiErrorMessage } from '$lib/utils/logger';
	import { createVisiblePoll } from '$lib/utils/visiblePoll';
	import { buildHistoryReuseTabData, buildReuseTabTitle } from '$lib/utils/historyReuse';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import type { ActiveGrid } from '$lib/generation/compare/compareStore.svelte';
	import CompareGrid from '$lib/components/compare/view/CompareGrid.svelte';
	import GridCellViewer from '$lib/components/compare/view/GridCellViewer.svelte';
	import GridExport from '$lib/components/compare/view/GridExport.svelte';
	import { fetchGrid, openGridId, retryGridFailed } from '$lib/generation/compare/view/gridApi';
	import { gridDimensions, gridProgress, gridTitle } from '$lib/generation/compare/view/gridModel';

	let { gridId, onClose }: { gridId: string; onClose: () => void } = $props();

	const POLL_MS = 2500;

	let grid = $state<ActiveGrid | null>(null);
	let failure = $state<string | null>(null);
	let viewIndex = $state<number | null>(null);
	let exporting = $state(false);
	let confirmingDelete = $state(false);
	let stopped = false;
	const poll = createVisiblePoll(load, POLL_MS);

	let viewing = $derived(grid && viewIndex !== null && viewIndex < grid.cells.length ? viewIndex : null);

	function syncPoll() {
		if (grid && gridProgress(grid).active) poll.start();
		else poll.stop();
	}

	async function load() {
		try {
			grid = await fetchGrid(gridId);
			failure = null;
		} catch (error) {
			failure = getErrorMessage(error);
			logger.error('Failed to load the grid', failure);
		}
		if (!stopped) syncPoll();
	}

	$effect(() => {
		void gridId;
		stopped = false;
		grid = null;
		void load();
		return () => {
			stopped = true;
			poll.stop();
		};
	});

	onDestroy(() => {
		stopped = true;
		poll.stop();
	});

	async function retry() {
		try {
			grid = await retryGridFailed(gridId);
			if (!stopped) syncPoll();
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

	async function removeGrid() {
		const target = gridId;
		confirmingDelete = false;
		close();
		try {
			await historyStore.deleteGrid(target);
		} catch (error) {
			toasts.error(getApiErrorMessage(error));
		}
	}

	function close() {
		historyStore.setSelectedGeneration(null);
		openGridId.set(null);
		onClose();
	}
</script>

<BaseModal isOpen={true} title="Compare grid" size="full" closeable={viewing === null && !exporting && !confirmingDelete} on:close={close}>
	<svelte:fragment slot="headerIcon">
		<div class="flex h-[30px] w-[30px] flex-shrink-0 items-center justify-center rounded border border-line bg-surface-2">
			<Icon name="grid" className="h-4 w-4 text-fg-muted" />
		</div>
	</svelte:fragment>
	<svelte:fragment slot="header">
		{#if grid}
			<span class="font-mono text-xs tabular-nums text-fg-subtle" data-testid="history-grid-dims">
				{gridTitle(grid.config)} · {gridDimensions(grid)}
			</span>
			<Tooltip text="Delete compare">
				<IconButton
					icon="trash"
					label="Delete compare"
					size="sm"
					onclick={() => (confirmingDelete = true)}
				/>
			</Tooltip>
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

{#if grid && confirmingDelete}
	<ConfirmModal
		isOpen={true}
		title="Delete X/Y Compare"
		message={gridDeleteMessage(grid.cols * grid.rows)}
		variant="danger"
		confirmLabel="Delete"
		on:confirm={removeGrid}
		on:cancel={() => (confirmingDelete = false)}
	/>
{/if}
