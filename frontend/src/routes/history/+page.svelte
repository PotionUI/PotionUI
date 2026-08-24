<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount, onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import { historyStore } from '$lib/stores/history';
	import { historyCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { tabsStore } from '$lib/stores/tabs';
	import { WebSocketService, createGenerationSocket, type WebSocketMessage } from '$lib/services/websocket';
	import { getBackends, type Backend } from '$lib/services/admin-api';
	import { buildHistoryReuseTabData } from '$lib/utils/historyReuse';
	import { toasts } from '$lib/stores/toast';
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
	import HistoryDeleteByTagsModal from './components/HistoryDeleteByTagsModal.svelte';
	import HistoryCompareModal from './components/HistoryCompareModal.svelte';
	import type { GenerationHistoryItem } from '$lib/types/history';

	let showDeleteModal = false;
	let generationToDelete: GenerationHistoryItem | null = null;
	let showAddTagModal = false;
	let showBulkDeleteModal = false;
	let showUploadModal = false;
	let showDeleteByTagsModal = false;
	let showCompareModal = false;
	let sidebarOpen = true;

	// Lazily fetched + cached on first reuse click — only needed to resolve
	// whether a generation's original backend is still around.
	let availableBackends: Backend[] | null = null;
	async function loadAvailableBackends(): Promise<Backend[]> {
		if (availableBackends) return availableBackends;
		try {
			const response = await getBackends();
			availableBackends = response.data ?? [];
		} catch (error) {
			logger.error('Failed to load backends for history reuse:', error);
			availableBackends = [];
		}
		return availableBackends;
	}

	$: currentState = $historyStore;
	$: availableTags = currentState.availableTags;

	// The two selected generations for the compare modal (exactly 2 when enabled).
	$: compareItems = currentState.generations.filter((gen) =>
		currentState.selectedGenerationIds.includes(gen.id)
	);

	function handleCompareClick() {
		if (compareItems.length === 2) {
			showCompareModal = true;
		}
	}

	// ── Live status updates ────────────────────────────────────────────────
	// Subscribe to the generation WebSocket for any in-progress generations
	// currently shown, so their status/progress update without a manual refresh.
	let ws: WebSocketService | null = null;
	const subscribedIds = new Set<string>();
	let reloadTimer: ReturnType<typeof setTimeout> | undefined;

	$: activeIds = currentState.generations
		.filter((g) => g.status === 'pending' || g.status === 'running')
		.map((g) => g.id);

	// Re-sync subscriptions whenever the socket is ready or the active set changes.
	$: if (ws) syncSubscriptions(activeIds);

	function scheduleReload() {
		if (reloadTimer) clearTimeout(reloadTimer);
		// Coalesce bursts of completions into one silent refetch that pulls final files.
		reloadTimer = setTimeout(() => historyStore.loadGenerations({ silent: true, merge: true }), 400);
	}

	// ── Discover new generations (e.g. started in another tab/device) ──────────
	// The WebSocket only streams ids we already know about, so we also poll the
	// first page in the background to pick up generations we haven't loaded yet.
	let pollTimer: ReturnType<typeof setInterval> | undefined;
	const POLL_MS = 6000;

	function pollTick() {
		if (typeof document !== 'undefined' && document.hidden) return;
		// New generations land at the top of page 1 (created_at desc); only that
		// view can surface them, so skip the poll elsewhere.
		if (currentState.currentPage !== 1) return;
		historyStore.loadGenerations({ silent: true, merge: true });
	}

	function handleVisibility() {
		if (typeof document !== 'undefined' && document.hidden) return;
		// Refresh immediately when the tab regains focus.
		if (currentState.currentPage === 1) historyStore.loadGenerations({ silent: true, merge: true });
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

		// On a terminal state, refetch so the finished thumbnail/files appear.
		if (status === 'completed' || status === 'failed' || status === 'cancelled') {
			scheduleReload();
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

		ws = createGenerationSocket();
		ws.connect();

		pollTimer = setInterval(pollTick, POLL_MS);
		document.addEventListener('visibilitychange', handleVisibility);
	});

	onDestroy(() => {
		if (reloadTimer) clearTimeout(reloadTimer);
		if (pollTimer) clearInterval(pollTimer);
		if (typeof document !== 'undefined') {
			document.removeEventListener('visibilitychange', handleVisibility);
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

	async function handleReuseRequest(generation: GenerationHistoryItem) {
		if (!generation.preset_id) return;

		const tabName = `Reused: ${generation.preset_name ?? generation.preset_id.split('/').pop()}`;
		const backends = await loadAvailableBackends();
		const { tabData, backendUnavailable } = buildHistoryReuseTabData(generation, backends);

		tabsStore.addTabWithData(tabName, tabData);

		if (backendUnavailable) {
			toasts.info('Original backend is no longer available — using the default backend.');
		}

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
				onOpenDeleteByTags={() => (showDeleteByTagsModal = true)}
			/>
			<HistoryTagsBar />
		</div>

		<HistorySelectionToolbar
			onBulkDeleteClick={() => (showBulkDeleteModal = true)}
			onCompareClick={handleCompareClick}
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

<!-- Delete by Tags Modal -->
{#if showDeleteByTagsModal}
	<HistoryDeleteByTagsModal onClose={() => (showDeleteByTagsModal = false)} />
{/if}

<!-- Compare Modal -->
{#if showCompareModal && compareItems.length === 2}
	<HistoryCompareModal
		left={compareItems[0]}
		right={compareItems[1]}
		onClose={() => (showCompareModal = false)}
	/>
{/if}

<!-- Upload Generation Modal -->
<UploadGenerationModal
	isOpen={showUploadModal}
	{availableTags}
	on:close={() => (showUploadModal = false)}
	on:success={() => (showUploadModal = false)}
/>
