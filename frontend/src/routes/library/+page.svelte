<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { get } from 'svelte/store';
	import { page } from '$app/stores';
	import { createUrlState } from '$lib/utils/urlState';
	import { hrefWithLibraryItem, libraryItemIdFromUrl } from '$lib/library/deepLink';
	import { takePendingFresh } from '$lib/library/freshHandoff';
	import { libraryStore } from '$lib/stores/library';
	import { libraryCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { toasts } from '$lib/stores/toast';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import CreateTagModal from '$lib/components/modals/CreateTagModal.svelte';
	import DeleteByCriteriaModal from '$lib/components/modals/DeleteByCriteriaModal.svelte';
	import type { DeleteByCriteriaCriteriaConfig } from '$lib/components/modals/deleteByCriteria';
	import { api } from '$lib/services/api/index';
	import LibraryToolbar from './components/LibraryToolbar.svelte';
	import LibraryTagsBar from './components/LibraryTagsBar.svelte';
	import LibrarySidebar from './components/LibrarySidebar.svelte';
	import LibrarySelectionToolbar from './components/LibrarySelectionToolbar.svelte';
	import LibraryGrid from './components/LibraryGrid.svelte';
	import LibraryItemModal from '$lib/components/library/LibraryItemModal.svelte';
	import { libraryItemDisplayName } from '$lib/library/libraryItemMeta';
	import type { LibraryItem } from '$lib/services/api/library';
	import ToolHost from '$lib/components/tools/ToolHost.svelte';
	import { registerCoreHistoryTools } from '../history/tools/coreTools';
	import { loadPluginMediaTools } from '$lib/tools/pluginTools';
	import { pickTool } from '$lib/tools/toolPicker';
	import { plainLetter, pageOwnsKeys } from '$lib/utils/shortcutGuard';
	import { selectionDeleteIntent } from '$lib/utils/deleteShortcut';
	import { gridShortcutTarget } from '$lib/tools/gridShortcut';
	import { buildLibraryToolContext, listTools } from '$lib/tools/tools';
	import { editMediaAvailability } from '$lib/tools/editMedia';
	import type { EditedMediaItem } from '$lib/services/api/media';
	import type { MediaTool, MediaToolContext } from '$lib/tools/tools';

	registerCoreHistoryTools();

	// The tool picked from the selection bar's Tools menu, with the selection
	// snapshot it was picked for.
	let activeTool: MediaTool | null = null;
	let activeToolContext: MediaToolContext | null = null;

	function handleToolSelect(tool: MediaTool, context: MediaToolContext) {
		pickTool(tool, context, {
			mount: (picked, pickedContext) => {
				activeTool = picked;
				activeToolContext = pickedContext;
			},
			onResult: handleToolResult
		});
	}

	async function handleToolResult(
		items: EditedMediaItem[],
		context: MediaToolContext,
		info: { replaced: boolean }
	) {
		if (items.length > 0) {
			await libraryStore.applyToolResult(
				items,
				context.items.map((item) => item.id),
				{ replaced: info.replaced }
			);
		}
		if (!context.entry) libraryStore.clearSelection();
	}

	function focusedItemId(): string | null {
		const active = document.activeElement as HTMLElement | null;
		return active?.closest?.('[data-library-card]')?.getAttribute('data-library-card') ?? null;
	}

	function handleGridShortcut(event: KeyboardEvent) {
		if (plainLetter(event) !== 'e' || !pageOwnsKeys(event)) return;
		const selected = $libraryStore.selectedIds;
		const id = gridShortcutTarget(selected, focusedItemId());
		if (!id) return;
		const item = $libraryStore.items.find((candidate) => candidate.id === id);
		if (!item) return;
		const context = buildLibraryToolContext([item], null);
		const tool = listTools('library').find((candidate) => candidate.id === 'edit-image');
		if (!tool || !editMediaAvailability(context).enabled) return;
		event.preventDefault();
		handleToolSelect(tool, context);
	}

	function handleDeleteShortcut(event: KeyboardEvent) {
		const intent = selectionDeleteIntent(event, pageOwnsKeys(event), $libraryStore.selectedIds);
		if (!intent) return;
		event.preventDefault();
		const single = intent.kind === 'single' ? $libraryStore.items.find((item) => item.id === intent.id) : null;
		if (single) itemToDelete = single;
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
		if (!entryScoped) libraryStore.clearSelection();
	}

	let sidebarOpen = true;
	let itemToDelete: LibraryItem | null = null;
	let showBulkDeleteModal = false;
	let showAddTagModal = false;
	let showDeleteByCriteriaModal = false;
	let deleting = false;
	let fileInput: HTMLInputElement;

	const LIBRARY_DELETE_CRITERIA_CONFIG: DeleteByCriteriaCriteriaConfig = {
		tags: true,
		age: true,
		mediaType: true
	};
	const LIBRARY_MEDIA_TYPE_OPTIONS = [
		{ value: 'image', label: 'Images' },
		{ value: 'video', label: 'Videos' },
		{ value: 'audio', label: 'Audio' }
	];

	$: state = $libraryStore;
	$: currentUrl = $page.url;

	let lastUrlItemId: string | null = null;
	let lastSelectedItemId: string | null = null;

	const itemUrl = createUrlState<string | null>({
		href: (id) => hrefWithLibraryItem(currentUrl, id)
	});

	function linkedItemMissing() {
		toasts.error("That library item couldn't be found");
		lastUrlItemId = null;
		void itemUrl.replace(null);
	}

	async function openLinkedItem(id: string) {
		const known = get(libraryStore).items.find((item) => item.id === id);
		if (known) {
			libraryStore.setSelectedItem(known);
			return;
		}
		try {
			const response = await api.getLibraryItem(id);
			if (lastUrlItemId !== id) return;
			if (response.success && response.data) libraryStore.setSelectedItem(response.data);
			else linkedItemMissing();
		} catch {
			if (lastUrlItemId === id) linkedItemMissing();
		}
	}

	function syncItemFromUrl(urlId: string | null) {
		if (urlId === lastUrlItemId) return;
		lastUrlItemId = urlId;
		const selectedId = get(libraryStore).selectedItem?.id ?? null;
		if (!urlId) {
			if (selectedId) libraryStore.setSelectedItem(null);
			return;
		}
		if (selectedId !== urlId) void openLinkedItem(urlId);
	}

	function syncItemToUrl(selectedId: string | null) {
		if (selectedId === lastSelectedItemId) return;
		lastSelectedItemId = selectedId;
		if (selectedId === lastUrlItemId) return;
		lastUrlItemId = selectedId;
		void itemUrl.replace(selectedId);
	}

	$: syncItemFromUrl(libraryItemIdFromUrl($page.url));
	$: syncItemToUrl(state.selectedItem?.id ?? null);

	onMount(() => {
		libraryStore.clearFresh();
		libraryStore.restoreItemsPerPage();
		libraryStore
			.load()
			.then(() => {
				libraryStore.markFresh(takePendingFresh());
				return libraryStore.loadTags();
			});
		libraryStore.loadFacets();
		collectionsStore.load();
		void loadPluginMediaTools();
	});

	onDestroy(() => libraryStore.clearFresh());

	function pickFiles() {
		fileInput?.click();
	}

	async function handleFilesChosen(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const files = Array.from(input.files ?? []);
		// Clear the input first so re-picking the same file still fires a change.
		input.value = '';
		if (files.length === 0) return;

		const { uploaded, failed, refusal } = await libraryStore.upload(files);
		if (uploaded > 0) {
			toasts.success(`Added ${uploaded} file${uploaded === 1 ? '' : 's'} to your library`);
		}
		if (refusal) {
			toasts.error(refusal);
		} else if (failed > 0) {
			toasts.error(`${failed} file${failed === 1 ? '' : 's'} could not be uploaded`);
		}
	}

	async function confirmDelete() {
		if (!itemToDelete) return;
		deleting = true;
		try {
			const response = await libraryStore.deleteItem(itemToDelete.id);
			if (response.success) {
				itemToDelete = null;
			} else {
				toasts.error('Could not delete that item');
			}
		} finally {
			deleting = false;
		}
	}

	async function confirmBulkDelete() {
		deleting = true;
		try {
			const { deleted, failed } = await libraryStore.bulkDelete();
			showBulkDeleteModal = false;
			if (deleted > 0) toasts.success(`Deleted ${deleted} item${deleted === 1 ? '' : 's'}`);
			if (failed > 0) toasts.error(`${failed} item${failed === 1 ? '' : 's'} could not be deleted`);
		} finally {
			deleting = false;
		}
	}
</script>

<svelte:window on:keydown={handlePageKeys} />

<svelte:head>
	<title>Library · PotionUI</title>
</svelte:head>

<input
	bind:this={fileInput}
	type="file"
	accept="image/*,video/*,audio/*"
	multiple
	class="hidden"
	on:change={handleFilesChosen}
/>

<div class="flex min-h-screen bg-canvas">
	<!-- Left folder-tree panel (collapsible), pinned while the gallery scrolls -->
	{#if sidebarOpen}
		<aside
			class="hidden md:block w-[272px] flex-shrink-0 self-stretch min-h-screen border-r border-line bg-surface-1 z-20"
		>
			<div class="sticky top-0 h-screen overflow-hidden">
				<LibrarySidebar onCollapse={() => (sidebarOpen = false)} />
			</div>
		</aside>
	{/if}

	<div class="flex-1 min-w-0">
		<div class="sticky top-0 z-30">
			<LibraryToolbar
				{sidebarOpen}
				onOpenSidebar={() => (sidebarOpen = true)}
				onOpenAddTag={() => (showAddTagModal = true)}
				onPickFiles={pickFiles}
				onOpenDeleteByCriteria={() => (showDeleteByCriteriaModal = true)}
			/>
			<LibraryTagsBar />
		</div>

		<LibrarySelectionToolbar
			onBulkDeleteClick={() => (showBulkDeleteModal = true)}
			onToolSelect={handleToolSelect}
		/>

		<LibraryGrid
				onDeleteRequest={(item) => (itemToDelete = item)}
				onBulkDeleteRequest={() => (showBulkDeleteModal = true)}
				onToolSelect={handleToolSelect}
				onUploadRequest={pickFiles}
			/>
	</div>
</div>

{#if state.selectedItem}
	<LibraryItemModal
		item={state.selectedItem}
		onClose={() => libraryStore.setSelectedItem(null)}
		onDeleteRequest={(item) => (itemToDelete = item)}
	/>
{/if}

{#if itemToDelete}
	<ConfirmModal
		isOpen={true}
		title="Delete library item"
		message={`Delete "${libraryItemDisplayName(itemToDelete)}" from your library? The file is removed from disk and this cannot be undone.`}
		variant="danger"
		busy={deleting}
		on:confirm={confirmDelete}
		on:cancel={() => (itemToDelete = null)}
	/>
{/if}

{#if showBulkDeleteModal}
	<ConfirmModal
		isOpen={true}
		title="Delete library items"
		message={`Delete ${state.selectedIds.length} item(s) from your library? The files are removed from disk and this cannot be undone.`}
		variant="danger"
		busy={deleting}
		on:confirm={confirmBulkDelete}
		on:cancel={() => (showBulkDeleteModal = false)}
	/>
{/if}

{#if showAddTagModal}
	<CreateTagModal
		onClose={() => (showAddTagModal = false)}
		onCreate={(name) => libraryStore.createTag(name)}
		description="Tags help you organize and filter your library. You can add multiple tags to each item."
	/>
{/if}

{#if showDeleteByCriteriaModal}
	<DeleteByCriteriaModal
		title="Delete Library Items by Criteria"
		itemLabel="item"
		criteria={LIBRARY_DELETE_CRITERIA_CONFIG}
		availableTags={state.availableTags}
		mediaTypeOptions={LIBRARY_MEDIA_TYPE_OPTIONS}
		count={async (request) => {
			const response = await api.countLibraryItemsByCriteria(request);
			return response.success && response.data ? response.data.count : 0;
		}}
		remove={async (request) => {
			const response = await libraryStore.bulkDeleteByCriteria(request);
			if (!response.success) throw new Error(response.message || 'Failed to delete library items by criteria');
			return response.data ?? { deleted_count: 0, files_deleted: 0 };
		}}
		onClose={() => (showDeleteByCriteriaModal = false)}
	/>
{/if}

<!-- The one mount point for whichever tool the Tools menu picked -->
<ToolHost tool={activeTool} context={activeToolContext} onClose={closeTool} onDone={finishTool} />
