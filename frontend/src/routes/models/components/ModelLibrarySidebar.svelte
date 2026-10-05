<script lang="ts">
	import { onMount } from 'svelte';
	import { modelLibraryStore } from '$lib/stores/modelLibrary';
	import CollectionLibrarySidebar from '$lib/components/collections/CollectionLibrarySidebar.svelte';
	import type { SmartView, TreeActions } from '$lib/components/collections/types';

	export let activeCollectionId: string | undefined;
	export let favoritesOnly: boolean;
	export let unsorted = false;
	export let directOnly = false;
	export let onSelectAll: () => void;
	export let onSelectFavorites: () => void;
	export let onSelectUnsorted: () => void;
	export let onSelectCollection: (id: string) => void;
	export let onDirectOnlyChange: (directOnly: boolean) => void;
	export let onCollapse: () => void;

	$: collections = $modelLibraryStore.collections;
	$: smartCounts = $modelLibraryStore.smartCounts;
	$: isAll = !activeCollectionId && !favoritesOnly && !unsorted;
	$: void modelLibraryStore.setDirectOnly(directOnly);

	onMount(() => {
		modelLibraryStore.load();
	});

	async function handleDelete(id: string, blockedIds: Set<string>) {
		const response = await modelLibraryStore.remove(id);
		if (response.success && activeCollectionId && blockedIds.has(activeCollectionId)) {
			onSelectCollection('');
		}
		return response;
	}

	$: allView = {
		id: 'all',
		icon: 'model',
		label: 'All models',
		active: isAll,
		count: smartCounts?.all,
		onSelect: onSelectAll
	} satisfies SmartView;
	$: favoritesView = {
		id: 'favorites',
		icon: 'heart',
		label: 'Favorites',
		active: favoritesOnly,
		count: smartCounts?.favorites,
		onSelect: onSelectFavorites
	} satisfies SmartView;
	$: unsortedView = {
		id: 'unsorted',
		icon: 'inbox',
		label: 'Unsorted',
		active: unsorted,
		count: smartCounts?.unsorted,
		onSelect: onSelectUnsorted
	} satisfies SmartView;

	const treeActions: TreeActions = {
		onSelect: onSelectCollection,
		onRename: (id, name) => modelLibraryStore.rename(id, name),
		onCreate: (name, parentId) => modelLibraryStore.create(name, parentId),
		onDelete: handleDelete,
		onMove: (id, parentId) => modelLibraryStore.move(id, parentId),
		onBulkMove: (ids, parentId) => modelLibraryStore.bulkMove(ids, parentId)
	};
</script>

<CollectionLibrarySidebar
	storageKey="models-expanded-collections"
	rulesScope="models"
	{collections}
	activeId={activeCollectionId}
	{allView}
	{favoritesView}
	{unsortedView}
	{directOnly}
	{onDirectOnlyChange}
	autoOrganizeSubject="models"
	{treeActions}
	onCreateRoot={(name) => modelLibraryStore.create(name, null)}
	{onCollapse}
/>
