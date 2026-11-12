<script lang="ts">
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import { showDeleteError } from '$lib/elixirs/openBrew';
	import { onMount, onDestroy } from 'svelte';
	import { generationIdOf } from '$lib/generation/messages/wire';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { get } from 'svelte/store';
	import {
		historyStore,
		selectedPosition,
		hasPreviousGeneration,
		hasNextGeneration
	} from '$lib/stores/history';
	import { toasts } from '$lib/stores/toast';
	import {
		createHistoryVersionWatcher,
		type HistoryVersionWatcher
	} from '$lib/stores/historyVersionWatcher';
	import { api } from '$lib/services/api/index';
	import { historyCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { tabsStore } from '$lib/stores/tabs';
	import { WebSocketService, createGenerationSocket, type WebSocketMessage } from '$lib/services/websocket';
	import { buildHistoryReuseTabData, buildReuseTabTitle } from '$lib/utils/historyReuse';
	import { announceReuseConversion } from '$lib/utils/reuseNotice';
	import { loadPresetNameMap } from '$lib/stores/presetsCatalog';
	import GenerationDetailsModal from '$lib/components/modals/GenerationDetailsModal.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import { createUrlState } from '$lib/utils/urlState';
	import { generationIdFromUrl, hrefWithGeneration } from '$lib/utils/deepLink';
	import UploadGenerationModal from '$lib/components/modals/UploadGenerationModal.svelte';
	import HistoryToolbar from './components/HistoryToolbar.svelte';
	import HistoryTagsBar from './components/HistoryTagsBar.svelte';
	import HistorySidebar from './components/HistorySidebar.svelte';
	import HistoryUsageCard from '$lib/plans/components/HistoryUsageCard.svelte';
	import HistorySelectionToolbar from './components/HistorySelectionToolbar.svelte';
	import HistoryGrid from './components/HistoryGrid.svelte';
	import HistoryGridView from './components/HistoryGridView.svelte';
	import GridDetailsCard from '$lib/components/compare/view/GridDetailsCard.svelte';
	import { fetchGrid, openGridId } from '$lib/generation/compare/view/gridApi';
	import { axisTag } from '$lib/generation/compare/view/gridModel';
	import type { ActiveGrid } from '$lib/generation/compare/compareStore.svelte';
	import HistoryDeleteModal from './components/HistoryDeleteModal.svelte';
	import HistoryAddTagModal from './components/HistoryAddTagModal.svelte';
	import HistoryBulkDeleteModal from './components/HistoryBulkDeleteModal.svelte';
	import DeleteByCriteriaModal from '$lib/components/modals/DeleteByCriteriaModal.svelte';
	import type { DeleteByCriteriaCriteriaConfig } from '$lib/components/modals/deleteByCriteria';
	import ToolHost from '$lib/components/tools/ToolHost.svelte';
	import { registerCoreHistoryTools } from './tools/coreTools';
	import { loadPluginMediaTools } from '$lib/tools/pluginTools';
	import { pickTool } from '$lib/tools/toolPicker';
	import { registerDetailsReturn } from '$lib/tools/detailsReturn';
	import { landOnHistoryResult } from '$lib/tools/historyResult';
	import { plainLetter, pageOwnsKeys } from '$lib/utils/shortcutGuard';
	import { selectionDeleteIntent } from '$lib/utils/deleteShortcut';
	import { gridShortcutTarget } from '$lib/tools/gridShortcut';
	import { buildHistoryToolContext, listTools } from '$lib/tools/tools';
	import { editMediaAvailability } from '$lib/tools/editMedia';
	import type { EditedMediaItem } from '$lib/services/api/media';
	import type { MediaTool, MediaToolContext } from '$lib/tools/tools';
	import type { GenerationHistoryItem } from '$lib/types/history';

	registerCoreHistoryTools();

	const HISTORY_DELETE_CRITERIA_CONFIG: DeleteByCriteriaCriteriaConfig = {
		tags: true,
		age: true,
		status: true,
		withoutMedia: true,
		keepFavorites: true
	};

	let showDeleteModal = false;
	let generationToDelete: GenerationHistoryItem | null = null;
	let showAddTagModal = false;
	let showBulkDeleteModal = false;
	let showUploadModal = false;
	let showDeleteByCriteriaModal = false;
	let sidebarOpen = true;
	let presetNamesCache: Record<string, string> = {};

	function resolvePresetName(id: string): string | null {
		return presetNamesCache[id] ?? null;
	}

	// The tool picked from the selection bar's Tools menu, with the selection
	// snapshot it was picked for.
	let activeTool: MediaTool | null = null;
	let activeToolContext: MediaToolContext | null = null;

	$: currentState = $historyStore;
	$: currentUrl = $page.url;

	let linkedMissingId: string | null = null;
	let lastUrlId: string | null = null;
	let lastSelectedId: string | null = null;

	const generationUrl = createUrlState<string | null>({
		href: (id) => hrefWithGeneration(currentUrl, id)
	});

	async function openLinkedGeneration(id: string, fileIndex = 0) {
		const known = get(historyStore).generations.find((gen) => gen.id === id);
		if (known) {
			historyStore.setSelectedGeneration(known, fileIndex);
			return;
		}
		try {
			const response = await api.getGenerationById(id, true, true);
			if (lastUrlId !== id) return;
			if (response.success && response.data) historyStore.setSelectedGeneration(response.data, fileIndex);
			else linkedMissingId = id;
		} catch (error) {
			logger.error('Failed to load the linked generation:', error);
			if (lastUrlId === id) linkedMissingId = id;
		}
	}

	function syncFromUrl(urlId: string | null) {
		if (urlId === lastUrlId) return;
		lastUrlId = urlId;
		linkedMissingId = null;
		const selectedId = get(historyStore).selectedGeneration?.id ?? null;
		if (!urlId) {
			if (selectedId) historyStore.setSelectedGeneration(null);
			return;
		}
		if (selectedId !== urlId) void openLinkedGeneration(urlId);
	}

	function syncToUrl(selectedId: string | null) {
		if (selectedId === lastSelectedId) return;
		lastSelectedId = selectedId;
		if (selectedId === lastUrlId) return;
		if (!selectedId && linkedMissingId) return;
		lastUrlId = selectedId;
		void generationUrl.replace(selectedId);
	}

	function closeMissingLink() {
		linkedMissingId = null;
		lastUrlId = null;
		void generationUrl.replace(null);
	}

	$: syncFromUrl(generationIdFromUrl($page.url));
	$: syncToUrl(currentState.selectedGeneration?.id ?? null);
	$: availableTags = currentState.availableTags;

	let detailGrid: ActiveGrid | null = null;
	let detailGridFor: string | null = null;

	async function loadDetailGrid(gridId: string | null) {
		if (gridId === detailGridFor) return;
		detailGridFor = gridId;
		detailGrid = null;
		if (!gridId) return;
		try {
			const loaded = await fetchGrid(gridId);
			if (detailGridFor === gridId) detailGrid = loaded;
		} catch (error) {
			logger.error('Failed to load the grid for this generation:', error);
		}
	}

	$: void loadDetailGrid(currentState.selectedGeneration?.grid_id ?? null);
	$: detailCellIndex =
		detailGrid && currentState.selectedGeneration
			? detailGrid.cells.findIndex((cell) => cell.generationId === currentState.selectedGeneration?.id)
			: -1;
	$: detailAxisTags = (() => {
		const tags: Record<string, string> = {};
		if (!detailGrid || !currentState.selectedGeneration) return tags;
		for (const field of Object.keys(currentState.selectedGeneration.axis_values ?? {})) {
			const tag = axisTag(field, detailGrid.config);
			if (tag) tags[field] = tag;
		}
		return tags;
	})();

	function openDetailGrid() {
		if (!detailGrid?.id) return;
		const id = detailGrid.id;
		handleModalClose();
		openGridId.set(id);
	}

	function handleToolSelect(tool: MediaTool, context: MediaToolContext) {
		pickTool(tool, context, {
			mount: (picked, pickedContext) => {
				activeTool = picked;
				activeToolContext = pickedContext;
			},
			onResult: handleToolResult
		});
	}

	function handleToolResult(items: EditedMediaItem[], context: MediaToolContext) {
		if (!context.entry) historyStore.clearSelection();
		const first = context.items[0];
		landOnHistoryResult(items, {
			source: first?.generationId ? { generationId: first.generationId, fileIndex: first.paramIndex ?? 0 } : null,
			startPath: window.location.pathname
		});
	}

	function focusedGenerationId(): string | null {
		const active = document.activeElement as HTMLElement | null;
		return active?.closest?.('[data-generation-card]')?.getAttribute('data-generation-id') ?? null;
	}

	function handleGridShortcut(event: KeyboardEvent) {
		if (plainLetter(event) !== 'e' || !pageOwnsKeys(event)) return;
		const selected = currentState.selectedGenerationIds;
		const id = gridShortcutTarget(selected, focusedGenerationId());
		if (!id) return;
		const context = buildHistoryToolContext(currentState.generations, [id], null);
		const tool = listTools('history').find((candidate) => candidate.id === 'edit-image');
		if (!tool || !editMediaAvailability(context).enabled) return;
		event.preventDefault();
		handleToolSelect(tool, context);
	}

	function handleDeleteShortcut(event: KeyboardEvent) {
		const intent = selectionDeleteIntent(event, pageOwnsKeys(event), currentState.selectedGenerationIds);
		if (!intent) return;
		event.preventDefault();
		const single = intent.kind === 'single' ? currentState.generations.find((gen) => gen.id === intent.id) : null;
		if (single) handleDeleteRequest(single);
		else showBulkDeleteModal = true;
	}

	function handlePageKeys(event: KeyboardEvent) {
		handleGridShortcut(event);
		handleDeleteShortcut(event);
	}

	function closeTool() {
		activeTool = null;
		activeToolContext = null;
	}

	function finishTool() {
		const entryScoped = !!activeToolContext?.entry;
		closeTool();
		if (!entryScoped) historyStore.clearSelection();
	}

	// ── Live status updates ────────────────────────────────────────────────
	// Subscribe to the generation WebSocket for any in-progress generations
	// currently shown, so their status/progress update without a manual refresh.
	let ws: WebSocketService | null = null;
	let unsubscribeConnection: (() => void) | null = null;
	const subscribedIds = new Set<string>();

	$: activeIds = currentState.generations
		.filter((g) => g.status === 'pending' || g.status === 'running')
		.map((g) => g.id);

	// Re-sync subscriptions whenever the socket is ready or the active set changes.
	$: if (ws) syncSubscriptions(activeIds);

	// ── Discover new generations (e.g. started in another tab/device) ──────────
	// The WebSocket only streams ids this tab already knows about, so a watcher
	// polls a cheap change-detection token and refetches the page only when it
	// moves. Pages past the first never auto-reload, as before: new work lands
	// at the top of page 1 (created_at desc) and nowhere else.
	let versionWatcher: HistoryVersionWatcher | null = null;

	function reloadHistory() {
		historyStore.loadGenerations({ silent: true, merge: true });
	}

	function handleVisibility() {
		versionWatcher?.setVisible(document.visibilityState === 'visible');
	}

	function handleFocus() {
		void versionWatcher?.check();
	}

	function handleWsMessage(message: WebSocketMessage) {
		const m = message as Record<string, any>;
		const generationId = generationIdOf(message);
		let status: string | null = null;
		let progress: number | undefined;

		if (m.type === 'generation_status') {
			status = m.status ?? null;
			progress = typeof m.progress === 'number' ? m.progress : undefined;
		} else if (m.type === 'generation_complete') {
			status = 'completed';
		} else if (m.type === 'generation_error') {
			status = 'failed';
		} else if (m.type === 'generation_cancelled') {
			status = 'cancelled';
		} else {
			return;
		}

		if (!generationId || !status) return;
		historyStore.applyLiveStatus(generationId, status, progress);

		// On a terminal state, refetch so the finished thumbnail/files appear. The
		// event already proves the list changed, so this refetches rather than
		// asking the version token whether it should.
		if (status === 'completed' || status === 'failed' || status === 'cancelled') {
			versionWatcher?.notifyChanged();
		}
	}

	function syncSubscriptions(ids: string[]) {
		if (!ws) return;
		const next = new Set(ids);
		for (const id of next) {
			if (!subscribedIds.has(id)) {
				ws.subscribe(id, handleWsMessage);
				subscribedIds.add(id);
			}
		}
		for (const id of [...subscribedIds]) {
			if (!next.has(id)) {
				ws.unsubscribe(id, handleWsMessage);
				subscribedIds.delete(id);
			}
		}
	}

	onMount(() => {
		const unregisterReturn = registerDetailsReturn((target) => {
			lastUrlId = target.generationId;
			void openLinkedGeneration(target.generationId, target.fileIndex);
		});
		historyStore.restoreItemsPerPage();
		if (new URLSearchParams(location.search).get('sort') === 'largest') historyStore.setSort('file_size', 'desc');
		historyStore.loadGenerations().then(() => historyStore.loadTags());
		historyStore.loadFacets();
		collectionsStore.load();
		void loadPluginMediaTools();
		loadPresetNameMap()
			.then((map) => (presetNamesCache = map))
			.catch((err) => logger.error('Failed to load preset names:', err));

		versionWatcher = createHistoryVersionWatcher({
			fetchVersion: async () => {
				const response = await api.getHistoryVersion();
				return response.success && response.data ? response.data.version : null;
			},
			reload: reloadHistory,
			canDiscover: () => currentState.currentPage === 1,
			isVisible: () => document.visibilityState === 'visible'
		});
		versionWatcher.start();

		ws = createGenerationSocket();
		// A socket that dropped may have swallowed events; reconcile on reconnect.
		unsubscribeConnection = ws.onConnectionChange((connected) =>
			versionWatcher?.connectionChanged(connected)
		);
		ws.connect();

		document.addEventListener('visibilitychange', handleVisibility);
		window.addEventListener('focus', handleFocus);
		return unregisterReturn;
	});

	onDestroy(() => {
		versionWatcher?.stop();
		versionWatcher = null;
		unsubscribeConnection?.();
		unsubscribeConnection = null;
		if (typeof document !== 'undefined') {
			document.removeEventListener('visibilitychange', handleVisibility);
			window.removeEventListener('focus', handleFocus);
		}
		if (ws) {
			for (const id of subscribedIds) ws.unsubscribe(id, handleWsMessage);
			subscribedIds.clear();
			ws.disconnect();
			ws = null;
		}
	});

	function handleDeleteRequest(generation: GenerationHistoryItem) {
		generationToDelete = generation;
		showDeleteModal = true;
	}

	async function confirmDelete() {
		const target = generationToDelete;
		if (!target) return;
		if (target.grid) {
			showDeleteModal = false;
			generationToDelete = null;
			try {
				await historyStore.deleteGrid(target.grid.id);
			} catch (error) {
				showDeleteError(error);
			}
			return;
		}
		try {
			await historyStore.deleteGeneration(target.id);
			const selected = get(historyStore).selectedGenerationIds;
			if (selected.includes(target.id)) historyStore.setSelection(selected.filter((id) => id !== target.id));
			showDeleteModal = false;
			generationToDelete = null;
		} catch (error) {
			logger.error('Failed to delete generation:', error);
			showDeleteError(error);
		}
	}

	function handleModalClose() {
		historyStore.setSelectedGeneration(null);
	}

	async function handleGenerationNavigate(direction: -1 | 1) {
		try {
			return await historyStore.selectAdjacentGeneration(direction);
		} catch (error) {
			logger.error('Failed to walk to the adjacent generation:', error);
			toasts.error(getErrorMessage(error));
			return false;
		}
	}

	function handleReuseRequest(generation: GenerationHistoryItem) {
		if (!generation.preset_id) return;

		const tabName = buildReuseTabTitle('Reused', generation, resolvePresetName);
		const reuse = buildHistoryReuseTabData(generation);

		tabsStore.addTabWithData(tabName, reuse.tabData);
		announceReuseConversion(reuse);

		handleModalClose();
		goto('/generate');
	}
</script>

<svelte:window on:keydown={handlePageKeys} />

<div class="flex h-[100dvh] bg-canvas">
	{#if sidebarOpen}
		<aside class="hidden md:flex w-[272px] flex-shrink-0 flex-col border-r border-line bg-surface-1 z-20">
			<div class="flex-1 min-h-0">
				<HistorySidebar onCollapse={() => (sidebarOpen = false)} />
			</div>
			<HistoryUsageCard />
		</aside>
	{/if}

	<div class="flex-1 min-w-0 flex flex-col min-h-0">
		<div class="flex-shrink-0 z-30">
			<HistoryToolbar
				{sidebarOpen}
				onOpenSidebar={() => (sidebarOpen = true)}
				onOpenUpload={() => (showUploadModal = true)}
				onOpenAddTag={() => (showAddTagModal = true)}
				onOpenDeleteByCriteria={() => (showDeleteByCriteriaModal = true)}
			/>
			<HistoryTagsBar />
		</div>

		<main class="flex-1 min-h-0 overflow-y-auto">
			<HistorySelectionToolbar
				onBulkDeleteClick={() => (showBulkDeleteModal = true)}
				onToolSelect={handleToolSelect}
			/>

			<HistoryGrid
				onDeleteRequest={handleDeleteRequest}
				onBulkDeleteRequest={() => (showBulkDeleteModal = true)}
				onReuseRequest={handleReuseRequest}
				onToolSelect={handleToolSelect}
			/>
		</main>
	</div>
</div>

<!-- Generation Details Modal -->
{#if currentState.selectedGeneration}
	<GenerationDetailsModal
		generation={currentState.selectedGeneration}
		isOpen={true}
		initialFileIndex={currentState.selectedFileIndex}
		onNavigate={handleGenerationNavigate}
		hasPrevious={$hasPreviousGeneration}
		hasNext={$hasNextGeneration}
		position={$selectedPosition}
		axisTags={detailAxisTags}
		axisValues={currentState.selectedGeneration?.axis_values ?? {}}
		on:close={handleModalClose}
		on:delete={(e) => handleDeleteRequest(e.detail)}
		on:reuse={(e) => handleReuseRequest(e.detail)}
	>
		<svelte:fragment slot="afterInformation">
			{#if detailGrid && detailCellIndex >= 0}
				<GridDetailsCard grid={detailGrid} index={detailCellIndex} onOpenGrid={openDetailGrid} />
			{/if}
		</svelte:fragment>
	</GenerationDetailsModal>
{/if}

{#if linkedMissingId}
	<ConfirmModal
		isOpen={true}
		title="Generation not found"
		message="This generation does not exist, or you do not have access to it."
		variant="info"
		confirmLabel="Back to History"
		on:confirm={closeMissingLink}
		on:cancel={closeMissingLink}
	/>
{/if}

{#if $openGridId}
	<HistoryGridView gridId={$openGridId} onClose={() => openGridId.set(null)} />
{/if}

<!-- Delete Confirmation Modal -->
{#if showDeleteModal && generationToDelete}
	<HistoryDeleteModal
		generation={generationToDelete}
		onCancel={() => (showDeleteModal = false)}
		onConfirm={confirmDelete}
	/>
{/if}

<!-- Add Tag Modal -->
{#if showAddTagModal}
	<HistoryAddTagModal onClose={() => (showAddTagModal = false)} />
{/if}

<!-- Bulk Delete Confirmation Modal -->
{#if showBulkDeleteModal}
	<HistoryBulkDeleteModal onClose={() => (showBulkDeleteModal = false)} />
{/if}

<!-- Delete by Criteria Modal -->
{#if showDeleteByCriteriaModal}
	<DeleteByCriteriaModal
		title="Delete Generations by Criteria"
		itemLabel="generation"
		criteria={HISTORY_DELETE_CRITERIA_CONFIG}
		availableTags={$historyStore.availableTags}
		count={async (request) => {
			const response = await api.countGenerationsByCriteria(request);
			return response.success && response.data ? response.data.count : 0;
		}}
		remove={async (request) => {
			const response = await historyStore.bulkDeleteByCriteria(request);
			if (!response.success) throw new Error(response.message || 'Failed to delete generations by criteria');
			return response.data ?? { deleted_count: 0, files_deleted: 0 };
		}}
		onClose={() => (showDeleteByCriteriaModal = false)}
	/>
{/if}

<!-- The one mount point for whichever tool the Tools menu picked -->
<ToolHost
	tool={activeTool}
	context={activeToolContext}
	onClose={closeTool}
	onDone={finishTool}
/>

<!-- Upload Generation Modal -->
<UploadGenerationModal
	isOpen={showUploadModal}
	{availableTags}
	on:close={() => (showUploadModal = false)}
	on:success={() => (showUploadModal = false)}
/>
