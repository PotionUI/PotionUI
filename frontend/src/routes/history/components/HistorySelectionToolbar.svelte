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
	import HistoryToolsMenu from './HistoryToolsMenu.svelte';
	import {
		buildHistoryToolContext,
		listHistoryToolGroups,
		type HistoryTool,
		type HistoryToolContext,
		type HistoryToolGroup
	} from '$lib/history/tools';
	import { historyToolRegistrations } from '$lib/history/pluginTools';

	// Self-contained: reads/writes historyStore directly. Only the bulk delete
	// confirmation modal and the picked tool's modal live on the page (need
	// shared state).
	export let onBulkDeleteClick: () => void;
	export let onToolSelect: (tool: HistoryTool, context: HistoryToolContext) => void;

	$: currentState = $historyStore;
	$: generations = $filteredGenerations;
	$: selectedIds = currentState.selectedGenerationIds;
	$: collections = $collectionsStore.collections;
	$: activeCollectionId = currentState.filters.collectionId;

	const { message: feedback, flash } = createFlashMessage();

	let copyingToLibrary = false;
	let selectingAllMatching = false;

	// Gmail's pattern: once every loaded item is selected and the filtered
	// history has more than the loaded page, offer to extend the selection to
	// every matching id (not just what happens to be on this page).
	$: allLoadedSelected = generations.length > 0 && generations.every((g) => selectedIds.includes(g.id));
	$: matchingAll =
		allLoadedSelected && currentState.totalCount > generations.length
			? {
					total: currentState.totalCount,
					busy: selectingAllMatching,
					onSelectAll: handleSelectAllMatching
				}
			: null;

	async function handleSelectAllMatching(): Promise<void> {
		selectingAllMatching = true;
		try {
			const result = await historyStore.selectAllMatching();
			if (!result) {
				flash('Failed to select all matching');
			} else if (result.truncated) {
				flash(`Selected ${result.count} of ${result.total} matching (capped)`);
			} else {
				flash(`Selected all ${result.total} matching`);
			}
		} finally {
			selectingAllMatching = false;
		}
	}

	$: toolContext = buildHistoryToolContext(generations, selectedIds, activeCollectionId ?? null);
	// The registry is a plain module map, so the plugin snapshot landing is the
	// only signal that the menu has to be rebuilt.
	$: toolGroups = groupsFor(toolContext, $historyToolRegistrations);

	function groupsFor(context: HistoryToolContext, _registrations: number): HistoryToolGroup[] {
		return listHistoryToolGroups(context);
	}

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
	{matchingAll}
	onAddToCollection={handleAddToCollection}
	onCreateAndAddToCollection={handleCreateAndAdd}
>
	<svelte:fragment slot="actionsBeforeCollection">
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

		<HistoryToolsMenu
			open={activeMenu === 'tools'}
			groups={toolGroups}
			onToggle={() => toggleMenu('tools')}
			onClose={closeMenus}
			onPick={(tool) => onToolSelect(tool, toolContext)}
		/>

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
