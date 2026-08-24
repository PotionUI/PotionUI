<script lang="ts">
	import { libraryStore } from '$lib/stores/library';
	import { libraryCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { api } from '$lib/services/api/index';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import { createFlashMessage } from '$lib/utils/flashMessage';
	import Icon from '$lib/components/Icon.svelte';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';

	// Self-contained: reads/writes libraryStore directly. Only the bulk delete
	// confirmation lives on the page (it needs shared modal state).
	export let onBulkDeleteClick: () => void;

	$: state = $libraryStore;
	$: selectedIds = state.selectedIds;
	$: collections = $collectionsStore.collections;
	$: activeCollectionId = state.filters.collectionId;

	const { message: feedback, flash } = createFlashMessage();

	async function handleAddToCollection(collectionId: string): Promise<boolean> {
		if (selectedIds.length === 0) return false;
		try {
			const response = await api.addUploadsToCollection(collectionId, selectedIds, 'library');
			if (response.success) {
				await collectionsStore.load();
				const added = response.data?.added ?? selectedIds.length;
				libraryStore.clearSelection();
				flash(`Added ${added} to collection`);
				return true;
			}
			flash('Failed to add to collection');
			return false;
		} catch (e) {
			logger.error('Add to collection failed:', getErrorMessage(e));
			flash('Failed to add to collection');
			return false;
		}
	}

	async function handleRemoveFromCollection(): Promise<void> {
		const collectionId = activeCollectionId;
		if (!collectionId || selectedIds.length === 0) return;
		try {
			const response = await api.removeUploadsFromCollection(collectionId, selectedIds, 'library');
			if (response.success) {
				await collectionsStore.load();
				const removed = response.data?.removed ?? selectedIds.length;
				libraryStore.clearSelection();
				flash(`Removed ${removed} from collection`);
				await libraryStore.load();
				return;
			}
			flash('Failed to remove from collection');
		} catch (e) {
			logger.error('Remove from collection failed:', getErrorMessage(e));
			flash('Failed to remove from collection');
		}
	}

	async function handleCreateAndAdd(name: string): Promise<boolean> {
		try {
			const created = await collectionsStore.create(name);
			const collection = created.success ? created.data?.collection : undefined;
			if (!collection) {
				flash('Failed to create collection');
				return false;
			}
			return await handleAddToCollection(collection.id);
		} catch (e) {
			logger.error('Create collection failed:', getErrorMessage(e));
			flash('Failed to create collection');
			return false;
		}
	}
</script>

<SelectionActionBar
	active={state.selectionMode}
	selectedCount={selectedIds.length}
	totalCount={state.items.length}
	onSelectAll={() => libraryStore.selectAll()}
	onClearSelection={() => libraryStore.clearSelection()}
	onClose={() => libraryStore.toggleSelectionMode()}
	feedback={$feedback}
	{collections}
	onAddToCollection={handleAddToCollection}
	onCreateAndAddToCollection={handleCreateAndAdd}
>
	<svelte:fragment slot="actionsAfterCollection">
		{#if activeCollectionId}
			<button
				class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5"
				title="Remove the selected items from this collection"
				on:click={handleRemoveFromCollection}
			>
				<Icon name="minus" className="w-4 h-4" />
				Remove from collection
			</button>
		{/if}

		<button
			class="px-4 py-1.5 bg-danger-solid text-white text-sm rounded hover:bg-danger-solid/90 transition-colors flex items-center gap-2 font-medium"
			on:click={onBulkDeleteClick}
		>
			<Icon name="trash" className="w-4 h-4" />
			Delete
		</button>
	</svelte:fragment>
</SelectionActionBar>
