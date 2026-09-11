<script lang="ts">
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import { onMount, onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
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
	import { buildHistoryReuseTabData } from '$lib/utils/historyReuse';
	import GenerationDetailsModal from '$lib/components/modals/GenerationDetailsModal.svelte';
	import UploadGenerationModal from '$lib/components/modals/UploadGenerationModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import HistoryToolbar from './components/HistoryToolbar.svelte';
	import HistoryTagsBar from './components/HistoryTagsBar.svelte';
	import HistorySidebar from './components/HistorySidebar.svelte';
	import HistorySelectionToolbar from './components/HistorySelectionToolbar.svelte';
	import HistoryGrid from './components/HistoryGrid.svelte';
	import HistoryDeleteModal from './components/HistoryDeleteModal.svelte';
	import HistoryAddTagModal from './components/HistoryAddTagModal.svelte';
	import HistoryBulkDeleteModal from './components/HistoryBulkDeleteModal.svelte';
	import HistoryDeleteByCriteriaModal from './components/HistoryDeleteByCriteriaModal.svelte';
	import HistoryToolHost from './components/HistoryToolHost.svelte';
	import { registerCoreHistoryTools } from './tools/coreTools';
	import { loadPluginHistoryTools } from '$lib/history/pluginTools';
	import type { HistoryTool, HistoryToolContext } from '$lib/history/tools';
	import type { GenerationHistoryItem } from '$lib/types/history';

	registerCoreHistoryTools();

	let showDeleteModal = false;
	let generationToDelete: GenerationHistoryItem | null = null;
	let showAddTagModal = false;
	let showBulkDeleteModal = false;
	let showUploadModal = false;
	let showDeleteByCriteriaModal = false;
	let sidebarOpen = true;

	// The tool picked from the selection bar's Tools menu, with the selection
	// snapshot it was picked for.
	let activeTool: HistoryTool | null = null;
	let activeToolContext: HistoryToolContext | null = null;

	$: currentState = $historyStore;
	$: availableTags = currentState.availableTags;

	function handleToolSelect(tool: HistoryTool, context: HistoryToolContext) {
		activeTool = tool;
		activeToolContext = context;
	}

	function closeTool() {
		activeTool = null;
		activeToolContext = null;
	}

	function finishTool() {
		closeTool();
		historyStore.clearSelection();
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
		let generationId: string | undefined = m.generation_id;
		let status: string | null = null;
		let progress: number | undefined;

		if (m.type === 'generation_status') {
			status = m.status ?? null;
			progress = typeof m.progress === 'number' ? m.progress : undefined;
		} else if (m.type === 'generation_complete') {
			status = 'completed';
			generationId = m.data?.id ?? m.data?.generation_id ?? generationId;
		} else if (m.type === 'generation_error') {
			status = 'failed';
			generationId = m.data?.generation_id ?? m.data?.id ?? generationId;
		} else if (m.type === 'generation_cancelled') {
			status = 'cancelled';
			generationId = m.data?.id ?? generationId;
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
		historyStore.restoreItemsPerPage();
		historyStore.loadGenerations().then(() => historyStore.loadTags());
		historyStore.loadFacets();
		collectionsStore.load();
		void loadPluginHistoryTools();

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
		if (generationToDelete) {
			try {
				await historyStore.deleteGeneration(generationToDelete.id);
				showDeleteModal = false;
				generationToDelete = null;
			} catch (error) {
				logger.error('Failed to delete generation:', error);
			}
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

		const tabName = `Reused: ${generation.preset_name ?? generation.preset_id.split('/').pop()}`;
		const { tabData } = buildHistoryReuseTabData(generation);

		tabsStore.addTabWithData(tabName, tabData);

		handleModalClose();
		goto('/generate');
	}
</script>

<div class="flex min-h-screen bg-canvas">
	<!-- Left folder-tree panel (collapsible), pinned while the gallery scrolls -->
	{#if sidebarOpen}
		<aside
			class="hidden md:block w-60 flex-shrink-0 self-stretch min-h-screen border-r border-line bg-surface-1 z-20"
		>
			<div class="sticky top-0 h-screen overflow-hidden">
				<HistorySidebar onCollapse={() => (sidebarOpen = false)} />
			</div>
		</aside>
	{:else}
		<aside class="hidden md:block w-8 flex-shrink-0 self-stretch min-h-screen border-r border-line bg-surface-1 z-20">
			<button
				class="sticky top-0 flex h-screen w-full flex-col items-center gap-2 pt-3 text-fg-subtle hover:text-fg hover:bg-surface-2 transition-colors"
				on:click={() => (sidebarOpen = true)}
				title="Show library"
				aria-label="Show library"
			>
				<Icon name="chevron-right" className="w-4 h-4" />
				<Icon name="folder" className="w-4 h-4" />
			</button>
		</aside>
	{/if}

	<!-- Right column: existing gallery content -->
	<div class="flex-1 min-w-0">
		<!-- Top Bar with Filters -->
		<div class="sticky top-0 z-30">
			<HistoryToolbar
				onOpenUpload={() => (showUploadModal = true)}
				onOpenAddTag={() => (showAddTagModal = true)}
				onOpenDeleteByCriteria={() => (showDeleteByCriteriaModal = true)}
			/>
			<HistoryTagsBar />
		</div>

		<HistorySelectionToolbar
			onBulkDeleteClick={() => (showBulkDeleteModal = true)}
			onToolSelect={handleToolSelect}
		/>

		<HistoryGrid onDeleteRequest={handleDeleteRequest} />
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
		on:close={handleModalClose}
		on:delete={(e) => handleDeleteRequest(e.detail)}
		on:reuse={(e) => handleReuseRequest(e.detail)}
	/>
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
	<HistoryDeleteByCriteriaModal onClose={() => (showDeleteByCriteriaModal = false)} />
{/if}

<!-- The one mount point for whichever tool the Tools menu picked -->
<HistoryToolHost
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
