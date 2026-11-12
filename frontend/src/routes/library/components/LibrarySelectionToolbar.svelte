<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import { libraryStore } from '$lib/stores/library';
	import { libraryCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { api } from '$lib/services/api/index';
	import { createFlashMessage } from '$lib/utils/flashMessage';
	import Icon from '$lib/components/Icon.svelte';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import { createMembershipActions } from '$lib/components/collections/scopeActions';
	import ToolsMenu from '$lib/components/tools/ToolsMenu.svelte';
	import {
		buildLibraryToolContext,
		groupToolsFor,
		type MediaTool,
		type MediaToolContext,
		type MediaToolGroup
	} from '$lib/tools/tools';
	import { mediaToolRegistrations } from '$lib/tools/pluginTools';
	import type { LibraryItem } from '$lib/services/api/library';

	// Self-contained: reads/writes libraryStore directly. Only the bulk delete
	// confirmation lives on the page (it needs shared modal state).
	export let onBulkDeleteClick: () => void;
	export let onAddToKit: (items: LibraryItem[]) => void = () => {};
	export let onToolSelect: (tool: MediaTool, context: MediaToolContext) => void;

	$: state = $libraryStore;
	$: selectedIds = state.selectedIds;
	$: selectedItems = state.items.filter((item) => selectedIds.includes(item.id));
	$: collections = $collectionsStore.collections;
	$: activeCollectionId = state.filters.collectionId;

	const { message: feedback, flash } = createFlashMessage();

	$: toolContext = buildLibraryToolContext(selectedItems, activeCollectionId ?? null);
	// The registry is a plain module map, so the plugin snapshot landing is the
	// only signal that the menu has to be rebuilt.
	$: toolGroups = groupsFor(toolContext, $mediaToolRegistrations);

	function groupsFor(context: MediaToolContext, _registrations: number): MediaToolGroup[] {
		return groupToolsFor(context, { surface: 'bar' });
	}

	const { addToCollection: handleAddToCollection, removeFromCollection, createAndAdd: handleCreateAndAdd } =
		createMembershipActions({
			getSelectedIds: () => selectedIds,
			add: (collectionId, ids) => api.addUploadsToCollection(collectionId, ids, 'library'),
			remove: (collectionId, ids) => api.removeUploadsFromCollection(collectionId, ids, 'library'),
			create: (name) => collectionsStore.create(name),
			reloadCollections: () => collectionsStore.load(),
			clearSelection: () => libraryStore.clearSelection(),
			afterRemove: () => libraryStore.load(),
			flash
		});

	function handleRemoveFromCollection(): Promise<void> {
		return removeFromCollection(activeCollectionId);
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
	<svelte:fragment slot="actionsAfterCollection" let:activeMenu let:toggleMenu let:closeMenus>
		{#if activeCollectionId}
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
			class="whitespace-nowrap px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5 disabled:opacity-50"
			disabled={selectedItems.length === 0}
			data-add-to-kit
			on:click={() => onAddToKit(selectedItems)}
		>
			<Icon name="layers" className="w-4 h-4" />
			Add to kit
		</button>

		<ToolsMenu
			open={activeMenu === 'tools'}
			scope="library"
			groups={toolGroups}
			ctx={toolContext}
			onToggle={() => toggleMenu('tools')}
			onClose={closeMenus}
			onPick={(tool) => onToolSelect(tool, toolContext)}
		/>

		<button
			class="whitespace-nowrap px-4 py-1.5 bg-danger-solid text-white text-sm rounded hover:bg-danger-solid/90 transition-colors flex items-center gap-2 font-medium"
			on:click={onBulkDeleteClick}
		>
			<Icon name="trash" className="w-4 h-4" />
			Delete
		</button>
	</svelte:fragment>
</SelectionActionBar>
