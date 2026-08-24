<script lang="ts">
	import { historyCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { historyStore } from '$lib/stores/history';
	import CollectionLibrarySidebar from '$lib/components/collections/CollectionLibrarySidebar.svelte';
	import type { SmartView, TreeActions } from '$lib/components/collections/types';

	export let onCollapse: () => void;

	$: collections = $collectionsStore.collections;
	$: activeId = $historyStore.filters.collectionId;
	$: favoritesOnly = !!$historyStore.filters.favoritesOnly;
	$: isAll = !activeId && !favoritesOnly;

	async function selectAll() {
		historyStore.setFilter('collectionId', undefined);
		historyStore.setFilter('favoritesOnly', false);
		await historyStore.loadGenerations();
	}

	async function selectFavorites() {
		historyStore.setFilter('favoritesOnly', true);
		historyStore.setFilter('collectionId', undefined);
		await historyStore.loadGenerations();
	}

	async function selectFolder(id: string) {
		historyStore.setFilter('collectionId', id);
		historyStore.setFilter('favoritesOnly', false);
		await historyStore.loadGenerations();
	}

	// Post-delete fallback: if the deleted folder (or one of its now-gone
	// descendants) was the active filter, fall back to "all generations".
	async function handleDelete(id: string, blockedIds: Set<string>) {
		const response = await collectionsStore.remove(id);
		if (response.success) {
			const state = $historyStore;
			if (state.filters.collectionId && blockedIds.has(state.filters.collectionId)) {
				historyStore.setFilter('collectionId', undefined);
				await historyStore.loadGenerations();
			}
		}
		return response;
	}

	$: smartViews = [
		{
			id: 'all',
			icon: 'image',
			label: 'All generations',
			active: isAll,
			onSelect: selectAll
		},
		{
			id: 'favorites',
			icon: 'heart',
			label: 'Favorites',
			active: favoritesOnly,
			onSelect: selectFavorites
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
	storageKey="history-expanded-collections"
	{collections}
	{activeId}
	{smartViews}
	{treeActions}
	onCreateRoot={(name) => collectionsStore.create(name, null)}
	{onCollapse}
/>
