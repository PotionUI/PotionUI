<script lang="ts">
	import { modelLibraryStore } from '$lib/stores/modelLibrary';
	import { api } from '$lib/services/api/index';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import { createFlashMessage } from '$lib/utils/flashMessage';
	import Icon from '$lib/components/Icon.svelte';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';

	// Self-contained floating action bar for the models grid's multi-select mode.
	// The models page keeps selection state locally (no global "modelsStore" the
	// way history has historyStore), so the selected ids / counts come in as
	// props instead of a store subscription.
	export let active: boolean;
	export let selectedIds: string[];
	export let totalOnPage: number;
	export let onSelectAll: () => void;
	export let onClearSelection: () => void;
	export let onClose: () => void;
	/** Called after a bulk favorite/unfavorite completes, so the page can refresh cards. */
	export let onFavoritesChanged: () => void;

	$: collections = $modelLibraryStore.collections;

	const { message: feedback, flash } = createFlashMessage();

	let favoriting = false;

	async function handleBulkFavorite(isFavorite: boolean) {
		if (selectedIds.length === 0) return;
		favoriting = true;
		try {
			await Promise.all(selectedIds.map((id) => api.setModelFavorite(id, isFavorite)));
			flash(isFavorite ? `Favorited ${selectedIds.length}` : `Unfavorited ${selectedIds.length}`);
			onFavoritesChanged();
		} catch (e) {
			logger.error('Bulk favorite failed:', getErrorMessage(e));
			flash('Failed to update favorites');
		} finally {
			favoriting = false;
		}
	}

	async function handleAddToCollection(collectionId: string): Promise<boolean> {
		if (selectedIds.length === 0) return false;
		try {
			const response = await api.addToModelCollection(collectionId, selectedIds);
			if (response.success) {
				await modelLibraryStore.load();
				const added = response.data?.added ?? selectedIds.length;
				onClearSelection();
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

	async function handleCreateAndAdd(name: string): Promise<boolean> {
		try {
			const created = await modelLibraryStore.create(name);
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
	{active}
	selectedCount={selectedIds.length}
	totalCount={totalOnPage}
	{onSelectAll}
	{onClearSelection}
	{onClose}
	feedback={$feedback}
	{collections}
	onAddToCollection={handleAddToCollection}
	onCreateAndAddToCollection={handleCreateAndAdd}
>
	<svelte:fragment slot="actionsBeforeCollection">
		<!-- Bulk favorite -->
		<button
			class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5 disabled:opacity-50"
			disabled={favoriting}
			title="Add selected to favorites"
			on:click={() => handleBulkFavorite(true)}
		>
			<Icon name="heart" className="w-4 h-4 text-signal" />
			Favorite
		</button>
		<button
			class="px-2 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors disabled:opacity-50"
			disabled={favoriting}
			title="Remove selected from favorites"
			on:click={() => handleBulkFavorite(false)}
		>
			<Icon name="heart" className="w-4 h-4" />
		</button>
	</svelte:fragment>
</SelectionActionBar>
