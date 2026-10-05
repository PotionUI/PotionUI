<script lang="ts">
	import { historyCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { historyStore } from '$lib/stores/history';
	import CollectionLibrarySidebar from '$lib/components/collections/CollectionLibrarySidebar.svelte';
	import type { SmartView, TreeActions } from '$lib/components/collections/types';

	export let onCollapse: () => void;

	$: collections = $collectionsStore.collections;
	$: smartCounts = $collectionsStore.smartCounts;
	$: activeId = $historyStore.filters.collectionId;
	$: favoritesOnly = !!$historyStore.filters.favoritesOnly;
	$: unsorted = !!$historyStore.filters.unsorted;
	$: directOnly = !!$historyStore.filters.directOnly;
	$: isAll = !activeId && !favoritesOnly && !unsorted;
	$: void collectionsStore.setDirectOnly(directOnly);

	async function applySelection(patch: {
		collectionId?: string;
		favoritesOnly?: boolean;
		unsorted?: boolean;
	}) {
		historyStore.setFilter('collectionId', patch.collectionId);
		historyStore.setFilter('favoritesOnly', !!patch.favoritesOnly);
		historyStore.setFilter('unsorted', !!patch.unsorted);
		await historyStore.loadGenerations();
	}

	async function setDirectOnly(value: boolean) {
		historyStore.setFilter('directOnly', value);
		await historyStore.loadGenerations();
	}

	async function handleDelete(id: string, blockedIds: Set<string>) {
		const response = await collectionsStore.remove(id);
		if (response.success) {
			const state = $historyStore;
			if (state.filters.collectionId && blockedIds.has(state.filters.collectionId)) {
				await applySelection({});
			}
		}
		return response;
	}

	$: allView = {
		id: 'all',
		icon: 'image',
		label: 'All generations',
		active: isAll,
		count: smartCounts?.all,
		onSelect: () => applySelection({})
	} satisfies SmartView;
	$: favoritesView = {
		id: 'favorites',
		icon: 'heart',
		label: 'Favorites',
		active: favoritesOnly,
		count: smartCounts?.favorites,
		onSelect: () => applySelection({ favoritesOnly: true })
	} satisfies SmartView;
	$: unsortedView = {
		id: 'unsorted',
		icon: 'inbox',
		label: 'Unsorted',
		active: unsorted,
		count: smartCounts?.unsorted,
		onSelect: () => applySelection({ unsorted: true })
	} satisfies SmartView;

	const treeActions: TreeActions = {
		onSelect: (id) => applySelection({ collectionId: id }),
		onRename: (id, name) => collectionsStore.rename(id, name),
		onCreate: (name, parentId) => collectionsStore.create(name, parentId),
		onDelete: handleDelete,
		onMove: (id, parentId) => collectionsStore.move(id, parentId),
		onBulkMove: (ids, parentId) => collectionsStore.bulkMove(ids, parentId)
	};
</script>

<CollectionLibrarySidebar
	storageKey="history-expanded-collections"
	rulesScope="history"
	{collections}
	{activeId}
	{allView}
	{favoritesView}
	{unsortedView}
	{directOnly}
	onDirectOnlyChange={setDirectOnly}
	autoOrganizeSubject="generations"
	{treeActions}
	onCreateRoot={(name) => collectionsStore.create(name, null)}
	{onCollapse}
/>
