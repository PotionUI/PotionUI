<script lang="ts">
	import { libraryCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { libraryStore } from '$lib/stores/library';
	import CollectionLibrarySidebar from '$lib/components/collections/CollectionLibrarySidebar.svelte';
	import type { SmartView, TreeActions } from '$lib/components/collections/types';

	export let onCollapse: () => void;

	$: collections = $collectionsStore.collections;
	$: smartCounts = $collectionsStore.smartCounts;
	$: activeId = $libraryStore.filters.collectionId;
	$: favoritesOnly = !!$libraryStore.filters.favoritesOnly;
	$: unsorted = !!$libraryStore.filters.unsorted;
	$: directOnly = !!$libraryStore.filters.directOnly;
	$: isAll = !activeId && !favoritesOnly && !unsorted;
	$: void collectionsStore.setDirectOnly(directOnly);

	async function applySelection(patch: {
		collectionId?: string;
		favoritesOnly?: boolean;
		unsorted?: boolean;
	}) {
		libraryStore.setFilter('collectionId', patch.collectionId);
		libraryStore.setFilter('favoritesOnly', !!patch.favoritesOnly);
		libraryStore.setFilter('unsorted', !!patch.unsorted);
		await libraryStore.load();
	}

	async function setDirectOnly(value: boolean) {
		libraryStore.setFilter('directOnly', value);
		await libraryStore.load();
	}

	async function handleDelete(id: string, blockedIds: Set<string>) {
		const response = await collectionsStore.remove(id);
		if (response.success) {
			const active = $libraryStore.filters.collectionId;
			if (active && blockedIds.has(active)) await applySelection({});
		}
		return response;
	}

	$: allView = {
		id: 'all',
		icon: 'photo',
		label: 'All uploads',
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
	storageKey="library-expanded-collections"
	{collections}
	{activeId}
	{allView}
	{favoritesView}
	{unsortedView}
	{directOnly}
	onDirectOnlyChange={setDirectOnly}
	autoOrganizeSubject="uploads"
	{treeActions}
	onCreateRoot={(name) => collectionsStore.create(name, null)}
	{onCollapse}
/>
