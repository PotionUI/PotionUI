<script lang="ts">
	import { libraryCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { libraryStore } from '$lib/stores/library';
	import CollectionLibrarySidebar from '$lib/components/collections/CollectionLibrarySidebar.svelte';
	import type { SmartView, TreeActions } from '$lib/components/collections/types';

	export let onCollapse: () => void;

	// Generations and library items share one folder tree, so this is the same
	// sidebar the history page renders - only the filter it drives differs.
	$: collections = $collectionsStore.collections;
	$: activeId = $libraryStore.filters.collectionId;

	async function selectAll() {
		libraryStore.setFilter('collectionId', undefined);
		await libraryStore.load();
	}

	async function selectFolder(id: string) {
		libraryStore.setFilter('collectionId', id);
		await libraryStore.load();
	}

	// Post-delete fallback: if the deleted folder (or one of its now-gone
	// descendants) was the active filter, fall back to the whole library.
	async function handleDelete(id: string, blockedIds: Set<string>) {
		const response = await collectionsStore.remove(id);
		if (response.success) {
			const active = $libraryStore.filters.collectionId;
			if (active && blockedIds.has(active)) {
				libraryStore.setFilter('collectionId', undefined);
				await libraryStore.load();
			}
		}
		return response;
	}

	$: smartViews = [
		{
			id: 'all',
			icon: 'photo',
			label: 'All items',
			active: !activeId,
			onSelect: selectAll
		}
	] satisfies SmartView[];

	const treeActions: TreeActions = {
		onSelect: selectFolder,
		onRename: (id, name) => collectionsStore.rename(id, name),
		onCreate: (name, parentId) => collectionsStore.create(name, parentId),
		onDelete: handleDelete,
		onMove: (id, parentId) => collectionsStore.move(id, parentId),
		onBulkMove: (ids, parentId) => collectionsStore.bulkMove(ids, parentId)
	};
</script>

<CollectionLibrarySidebar
	storageKey="library-expanded-collections"
	{collections}
	{activeId}
	{smartViews}
	{treeActions}
	onCreateRoot={(name) => collectionsStore.create(name, null)}
	{onCollapse}
/>
