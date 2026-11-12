<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import { historyStore, filteredGenerations } from '$lib/stores/history';
	import { historyCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { api } from '$lib/services/api/index';
	import { createFlashMessage } from '$lib/utils/flashMessage';
	import Icon from '$lib/components/Icon.svelte';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import { createMembershipActions } from '$lib/components/collections/scopeActions';
	import { libraryStore } from '$lib/stores/library';
	import { summarizeCopyOutcome } from '$lib/library/copyToLibrary';
	import { requestAddGenerationsToKit } from '$lib/stores/kitAddRequests';
	import ToolsMenu from '$lib/components/tools/ToolsMenu.svelte';
	import {
		buildHistoryToolContext,
		groupToolsFor,
		type MediaTool,
		type MediaToolContext,
		type MediaToolGroup
	} from '$lib/tools/tools';
	import { mediaToolRegistrations } from '$lib/tools/pluginTools';

	// Self-contained: reads/writes historyStore directly. Only the bulk delete
	// confirmation modal and the picked tool's modal live on the page (need
	// shared state).
	export let onBulkDeleteClick: () => void;
	export let onToolSelect: (tool: MediaTool, context: MediaToolContext) => void;

	$: currentState = $historyStore;
	$: generations = $filteredGenerations;
	$: selectedIds = currentState.selectedGenerationIds;
	$: collections = $collectionsStore.collections;
	$: activeCollectionId = currentState.filters.collectionId;

	const { message: feedback, flash } = createFlashMessage();

	let copyingToLibrary = false;

	$: toolContext = buildHistoryToolContext(generations, selectedIds, activeCollectionId ?? null);
	// The registry is a plain module map, so the plugin snapshot landing is the
	// only signal that the menu has to be rebuilt.
	$: toolGroups = groupsFor(toolContext, $mediaToolRegistrations);

	function groupsFor(context: MediaToolContext, _registrations: number): MediaToolGroup[] {
		return groupToolsFor(context, { surface: 'bar' });
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

	function handleAddToKit(): void {
		requestAddGenerationsToKit(generations.filter((generation) => selectedIds.includes(generation.id)));
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

	const { addToCollection: handleAddToCollection, removeFromCollection, createAndAdd: handleCreateAndAdd } =
		createMembershipActions({
			getSelectedIds: () => selectedIds,
			add: (collectionId, ids) => api.addToCollection(collectionId, ids, 'history'),
			remove: (collectionId, ids) => api.removeFromCollection(collectionId, ids, 'history'),
			create: (name) => collectionsStore.create(name),
			reloadCollections: () => collectionsStore.load(),
			clearSelection: () => historyStore.clearSelection(),
			afterRemove: () => historyStore.loadGenerations(),
			flash
		});

	function handleRemoveFromCollection(): Promise<void> {
		return removeFromCollection(activeCollectionId);
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
		<!-- Copy the selected generations' media into the private library -->
		<button
			class="whitespace-nowrap px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
			disabled={copyingToLibrary}
			use:tooltip={'Copy the selected media into your library (the generations stay in history)'}
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
				class="whitespace-nowrap px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5"
				use:tooltip={'Remove the selected items from this collection'}
				on:click={handleRemoveFromCollection}
			>
				<Icon name="minus" className="w-4 h-4" />
				Remove from collection
			</button>
		{/if}

		<button
			class="whitespace-nowrap px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
			disabled={selectedIds.length === 0}
			data-add-to-kit
			use:tooltip={'Add the selected media to a kit'}
			on:click={handleAddToKit}
		>
			<Icon name="layers" className="w-4 h-4" />
			Add to kit
		</button>

		<ToolsMenu
			open={activeMenu === 'tools'}
			scope="history"
			groups={toolGroups}
			ctx={toolContext}
			onToggle={() => toggleMenu('tools')}
			onClose={closeMenus}
			onPick={(tool) => onToolSelect(tool, toolContext)}
		/>

		<!-- Delete Button -->
		<button
			class="whitespace-nowrap px-4 py-1.5 bg-danger-solid text-white text-sm rounded hover:bg-danger-solid/90 transition-colors flex items-center gap-2 font-medium"
			on:click={onBulkDeleteClick}
		>
			<Icon name="trash" className="w-4 h-4" />
			Delete
		</button>
	</svelte:fragment>
</SelectionActionBar>
