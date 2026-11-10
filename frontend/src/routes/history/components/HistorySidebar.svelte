<script lang="ts">
	import { historyCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { historyStore } from '$lib/stores/history';
	import CollectionLibrarySidebar from '$lib/components/collections/CollectionLibrarySidebar.svelte';
	import CollectionActionsMenu from '$lib/components/collections/CollectionActionsMenu.svelte';
	import type { SmartView, TreeActions } from '$lib/components/collections/types';
	import { buildSmartViews, createFilterSelection } from '$lib/components/collections/scopeActions';
	import { createEntriesActions } from '$lib/components/collections/deleteEntries';
	import { api } from '$lib/services/api/index';

	let allView: SmartView;
	let favoritesView: SmartView;
	let unsortedView: SmartView;

	export let onCollapse: (() => void) | undefined = undefined;
	export let part: 'sidebar' | 'actions' = 'sidebar';

	$: collections = $collectionsStore.collections;
	$: smartCounts = $collectionsStore.smartCounts;
	$: activeId = $historyStore.filters.collectionId;
	$: activeCollection = collections.find((c) => c.id === activeId);
	$: favoritesOnly = !!$historyStore.filters.favoritesOnly;
	$: unsorted = !!$historyStore.filters.unsorted;
	$: directOnly = !!$historyStore.filters.directOnly;
	$: isAll = !activeId && !favoritesOnly && !unsorted;
	$: void collectionsStore.setDirectOnly(directOnly);

	const { applySelection } = createFilterSelection(
		(key, value) => historyStore.setFilter(key, value),
		() => historyStore.loadGenerations()
	);

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

	$: ({ allView, favoritesView, unsortedView } = buildSmartViews({
		icon: 'image',
		label: 'All generations',
		isAll,
		favoritesOnly,
		unsorted,
		counts: smartCounts,
		onAll: () => applySelection({}),
		onFavorites: () => applySelection({ favoritesOnly: true }),
		onUnsorted: () => applySelection({ unsorted: true })
	}));

	const treeActions: TreeActions = {
		onSelect: (id) => applySelection({ collectionId: id }),
		onRename: (id, name) => collectionsStore.rename(id, name),
		onCreate: (name, parentId) => collectionsStore.create(name, parentId),
		onDelete: handleDelete,
		onMove: (id, parentId) => collectionsStore.move(id, parentId),
		onBulkMove: (ids, parentId) => collectionsStore.bulkMove(ids, parentId),
		entries: createEntriesActions(
			'history',
			{ list: (scope, directOnly) => api.listCollections(scope, directOnly), deleteEntries: (id) => api.deleteCollectionEntries(id) },
			() => Promise.all([historyStore.loadGenerations(), collectionsStore.load()])
		)
	};
</script>

{#if part === 'actions'}
	{#if activeCollection}
		<CollectionActionsMenu
			variant="toolbar"
			item={activeCollection}
			{collections}
			{treeActions}
			rulesScope="history"
		/>
	{/if}
{:else}
	<CollectionLibrarySidebar
		storageKey="history-expanded-collections"
		label="History"
		rulesScope="history"
		{collections}
		{activeId}
		{allView}
		{favoritesView}
		{unsortedView}
		autoOrganizeSubject="generations"
		{treeActions}
		onCreateRoot={(name) => collectionsStore.create(name, null)}
		{onCollapse}
	/>
{/if}
