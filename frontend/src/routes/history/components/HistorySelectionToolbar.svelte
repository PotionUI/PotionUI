<script lang="ts">
	import { historyStore, filteredGenerations } from '$lib/stores/history';
	import { historyCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { api } from '$lib/services/api/index';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import { createFlashMessage } from '$lib/utils/flashMessage';
	import Icon from '$lib/components/Icon.svelte';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import { libraryStore } from '$lib/stores/library';
	import { summarizeCopyOutcome } from '$lib/library/copyToLibrary';

	// Self-contained: reads/writes historyStore directly. Only the bulk delete
	// confirmation modal and compare modal live on the page (need shared state).
	export let onBulkDeleteClick: () => void;
	export let onCompareClick: () => void;

	$: currentState = $historyStore;
	$: generations = $filteredGenerations;
	$: selectedIds = currentState.selectedGenerationIds;
	$: collections = $collectionsStore.collections;
	$: activeCollectionId = currentState.filters.collectionId;

	const { message: feedback, flash } = createFlashMessage();

	let stripMetadata = false;
	let exporting = false;
	let copyingToLibrary = false;

	// Copy, not move: the generations stay in history untouched and the copies
	// land in the library stripped of every generation field.
	async function handleCopyToLibrary(): Promise<void> {
		const selected = generations.filter((generation) => selectedIds.includes(generation.id));
		if (selected.length === 0) return;
		try {
			copyingToLibrary = true;
			const { copied, failed } = await libraryStore.copyFromGenerations(selected);
			// The selection deliberately survives: clearing it unmounts this bar,
			// and with it the only confirmation that the copy happened at all.
			flash(summarizeCopyOutcome(copied, failed));
		} finally {
			copyingToLibrary = false;
		}
	}

	function handleSelectAll() {
		historyStore.selectAll();
	}

	function handleClearSelection() {
		historyStore.clearSelection();
	}

	function handleToggleSelectionMode() {
		historyStore.toggleSelectionMode();
	}

	async function handleAddToCollection(collectionId: string): Promise<boolean> {
		if (selectedIds.length === 0) return false;
		try {
			const response = await api.addToCollection(collectionId, selectedIds, 'history');
			if (response.success) {
				await collectionsStore.load();
				const added = response.data?.added ?? selectedIds.length;
				historyStore.clearSelection();
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
			const response = await api.removeFromCollection(collectionId, selectedIds, 'history');
			if (response.success) {
				await collectionsStore.load();
				const removed = response.data?.removed ?? selectedIds.length;
				historyStore.clearSelection();
				flash(`Removed ${removed} from collection`);
				await historyStore.loadGenerations();
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

	async function handleExport(): Promise<boolean> {
		if (selectedIds.length === 0) return false;
		try {
			exporting = true;
			await api.exportGenerations(selectedIds, stripMetadata);
			flash('Export started');
			return true;
		} catch (e) {
			logger.error('Export failed:', getErrorMessage(e));
			flash('Export failed');
			return false;
		} finally {
			exporting = false;
		}
	}
</script>

<SelectionActionBar
	active={currentState.selectionMode}
	selectedCount={selectedIds.length}
	totalCount={generations.length}
	onSelectAll={handleSelectAll}
	onClearSelection={handleClearSelection}
	onClose={handleToggleSelectionMode}
	feedback={$feedback}
	{collections}
	onAddToCollection={handleAddToCollection}
	onCreateAndAddToCollection={handleCreateAndAdd}
>
	<svelte:fragment slot="actionsBeforeCollection">
		<!-- Compare (exactly 2) -->
		<button
			class="px-3 py-1.5 text-sm rounded transition-colors flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed {selectedIds.length ===
			2
				? 'text-fg-muted hover:text-fg hover:bg-surface-2'
				: 'text-fg-muted'}"
			disabled={selectedIds.length !== 2}
			title={selectedIds.length === 2
				? 'Compare the two selected generations'
				: 'Select exactly 2 to compare'}
			on:click={onCompareClick}
		>
			<Icon name="layers" className="w-4 h-4" />
			Compare
		</button>

		<!-- Copy the selected generations' media into the private library -->
		<button
			class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
			disabled={copyingToLibrary}
			title="Copy the selected media into your library (the generations stay in history)"
			on:click={handleCopyToLibrary}
		>
			<Icon name="photo" className="w-4 h-4" />
			{copyingToLibrary ? 'Copying…' : 'Copy to Library'}
		</button>
	</svelte:fragment>

	<svelte:fragment slot="actionsAfterCollection" let:activeMenu let:toggleMenu let:closeMenus>
		{#if activeCollectionId}
			<!-- Remove from the collection currently being browsed -->
			<button
				class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5"
				title="Remove the selected items from this collection"
				on:click={handleRemoveFromCollection}
			>
				<Icon name="minus" className="w-4 h-4" />
				Remove from collection
			</button>
		{/if}

		<!-- Download zip -->
		<div class="relative">
			<button
				class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5"
				on:click={() => toggleMenu('export')}
				aria-haspopup="menu"
				aria-expanded={activeMenu === 'export'}
			>
				<Icon name="download" className="w-4 h-4" />
				Download .zip
				<Icon name="chevron-up" className="w-3 h-3" />
			</button>
			{#if activeMenu === 'export'}
				<div
					class="absolute bottom-full mb-2 right-0 w-60 bg-surface-1 border border-line-strong rounded-lg shadow-overlay p-3 flex flex-col gap-3"
					role="menu"
				>
					<label class="flex items-center gap-2 text-sm text-fg-muted cursor-pointer">
						<input type="checkbox" bind:checked={stripMetadata} class="accent-accent" />
						Strip metadata
					</label>
					<button
						class="w-full px-3 py-1.5 bg-accent text-accent-contrast text-sm rounded hover:bg-accent-hover transition-colors flex items-center justify-center gap-2 disabled:opacity-50"
						disabled={exporting}
						on:click={async () => {
							if (await handleExport()) closeMenus();
						}}
					>
						{#if exporting}
							<span
								class="w-4 h-4 rounded-full border-2 border-line-strong border-t-current animate-spin"
							></span>
							Zipping…
						{:else}
							<Icon name="download" className="w-4 h-4" />
							Download {selectedIds.length}
						{/if}
					</button>
				</div>
			{/if}
		</div>

		<!-- Delete Button -->
		<button
			class="px-4 py-1.5 bg-danger-solid text-white text-sm rounded hover:bg-danger-solid/90 transition-colors flex items-center gap-2 font-medium"
			on:click={onBulkDeleteClick}
		>
			<Icon name="trash" className="w-4 h-4" />
			Delete
		</button>
	</svelte:fragment>
</SelectionActionBar>
