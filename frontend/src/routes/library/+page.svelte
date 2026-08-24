<script lang="ts">
	import { onMount } from 'svelte';
	import { libraryStore } from '$lib/stores/library';
	import { libraryCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { toasts } from '$lib/stores/toast';
	import Icon from '$lib/components/Icon.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import CreateTagModal from '$lib/components/modals/CreateTagModal.svelte';
	import LibraryToolbar from './components/LibraryToolbar.svelte';
	import LibraryTagsBar from './components/LibraryTagsBar.svelte';
	import LibrarySidebar from './components/LibrarySidebar.svelte';
	import LibrarySelectionToolbar from './components/LibrarySelectionToolbar.svelte';
	import LibraryGrid from './components/LibraryGrid.svelte';
	import LibraryItemModal from './components/LibraryItemModal.svelte';
	import { libraryItemDisplayName } from '$lib/library/libraryItemMeta';
	import type { LibraryItem } from '$lib/services/api/library';

	let sidebarOpen = true;
	let itemToDelete: LibraryItem | null = null;
	let showBulkDeleteModal = false;
	let showAddTagModal = false;
	let deleting = false;
	let fileInput: HTMLInputElement;

	$: state = $libraryStore;

	onMount(() => {
		libraryStore.restoreItemsPerPage();
		libraryStore.load().then(() => libraryStore.loadTags());
		libraryStore.loadFacets();
		collectionsStore.load();
	});

	function pickFiles() {
		fileInput?.click();
	}

	async function handleFilesChosen(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const files = Array.from(input.files ?? []);
		// Clear the input first so re-picking the same file still fires a change.
		input.value = '';
		if (files.length === 0) return;

		const { uploaded, failed } = await libraryStore.upload(files);
		if (uploaded > 0) {
			toasts.success(`Added ${uploaded} file${uploaded === 1 ? '' : 's'} to your library`);
		}
		if (failed > 0) {
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
			class="hidden md:block w-60 flex-shrink-0 self-stretch min-h-screen border-r border-line bg-surface-1 z-20"
		>
			<div class="sticky top-0 h-screen overflow-hidden">
				<LibrarySidebar onCollapse={() => (sidebarOpen = false)} />
			</div>
		</aside>
	{:else}
		<aside
			class="hidden md:block w-8 flex-shrink-0 self-stretch min-h-screen border-r border-line bg-surface-1 z-20"
		>
			<button
				class="sticky top-0 flex h-screen w-full flex-col items-center gap-2 pt-3 text-fg-subtle hover:text-fg hover:bg-surface-2 transition-colors"
				on:click={() => (sidebarOpen = true)}
				title="Show folders"
				aria-label="Show folders"
			>
				<Icon name="chevron-right" className="w-4 h-4" />
				<Icon name="folder" className="w-4 h-4" />
			</button>
		</aside>
	{/if}

	<div class="flex-1 min-w-0">
		<div class="sticky top-0 z-30">
			<LibraryToolbar
				onOpenAddTag={() => (showAddTagModal = true)}
				onPickFiles={pickFiles}
			/>
			<LibraryTagsBar />
		</div>

		<LibrarySelectionToolbar onBulkDeleteClick={() => (showBulkDeleteModal = true)} />

		<LibraryGrid onDeleteRequest={(item) => (itemToDelete = item)} onUploadRequest={pickFiles} />
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
